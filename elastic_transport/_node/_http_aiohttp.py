#  Licensed to Elasticsearch B.V. under one or more contributor
#  license agreements. See the NOTICE file distributed with
#  this work for additional information regarding copyright
#  ownership. Elasticsearch B.V. licenses this file to you under
#  the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
# 	http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.

import asyncio
import base64
import functools
import gzip
import os
import ssl
import warnings
from typing import Tuple

from .._compat import get_running_loop, warn_stacklevel
from .._exceptions import ConnectionError, ConnectionTimeout, SecurityWarning, TlsError
from .._models import ApiResponseMeta, HttpHeaders, NodeConfig
from ..client_utils import DEFAULT, client_meta_version, normalize_headers
from ._base import DEFAULT_CA_CERTS, RERAISE_EXCEPTIONS
from ._base_async import BaseAsyncNode

try:
    import aiohttp
    import aiohttp.client_exceptions as aiohttp_exceptions

    _AIOHTTP_AVAILABLE = True
    _AIOHTTP_META_VERSION = client_meta_version(aiohttp.__version__)
except ImportError:  # pragma: nocover
    _AIOHTTP_AVAILABLE = False
    _AIOHTTP_META_VERSION = ""


class AiohttpHttpNode(BaseAsyncNode):
    _ELASTIC_CLIENT_META = ("ai", _AIOHTTP_META_VERSION)

    def __init__(self, config: NodeConfig):
        if not _AIOHTTP_AVAILABLE:  # pragma: nocover
            raise ValueError("You must have 'aiohttp' installed to use AiohttpHttpNode")

        super().__init__(config)

        self._ssl_assert_fingerprint = config.ssl_assert_fingerprint
        if config.scheme == "https" and config.ssl_context is None:
            if config.ssl_version is None:
                ssl_context = ssl.create_default_context()
            else:
                ssl_context = ssl.SSLContext(config.ssl_version)

            if config.verify_certs:
                ssl_context.verify_mode = ssl.CERT_REQUIRED
                ssl_context.check_hostname = True
            else:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            ca_certs = DEFAULT_CA_CERTS if config.ca_certs is None else config.ca_certs
            if config.verify_certs:
                if not ca_certs:
                    raise ValueError(
                        "Root certificates are missing for certificate "
                        "validation. Either pass them in using the ca_certs parameter or "
                        "install certifi to use it automatically."
                    )
            else:
                if config.ssl_show_warn:
                    warnings.warn(
                        f"Connecting to {self.base_url!r} using TLS with verify_certs=False is insecure",
                        stacklevel=warn_stacklevel(),
                        category=SecurityWarning,
                    )

            if os.path.isfile(ca_certs):
                ssl_context.load_verify_locations(cafile=ca_certs)
            elif os.path.isdir(ca_certs):
                ssl_context.load_verify_locations(capath=ca_certs)
            else:
                raise ValueError("ca_certs parameter is not a path")

            # Use client_cert and client_key variables for SSL certificate configuration.
            if config.client_cert and not os.path.isfile(config.client_cert):
                raise ValueError("client_cert is not a path to a file")
            if config.client_key and not os.path.isfile(config.client_key):
                raise ValueError("client_key is not a path to a file")
            if config.client_cert and config.client_key:
                ssl_context.load_cert_chain(config.client_cert, config.client_key)
            elif config.client_cert:
                ssl_context.load_cert_chain(config.client_cert)

        self._loop = None
        self.session = None

        # Parameters for creating an aiohttp.ClientSession later.
        self._connections_per_node = config.connections_per_node
        self._ssl_context = config.ssl_context

    async def perform_request(
        self,
        method,
        target,
        body=None,
        request_timeout=DEFAULT,
        ignore_status=(),
        headers=None,
    ) -> Tuple[ApiResponseMeta, bytes]:
        if self.session is None:
            self._create_aiohttp_session()
        assert self.session is not None

        url = self.base_url + target

        # There is a bug in aiohttp that disables the re-use
        # of the connection in the pool when method=HEAD.
        # See: aio-libs/aiohttp#1769
        is_head = False
        if method == "HEAD":
            method = "GET"
            is_head = True

        # total=0 means no timeout for aiohttp
        request_timeout = (
            request_timeout
            if (request_timeout is not DEFAULT)
            else self.config.request_timeout
        )
        aiohttp_timeout = aiohttp.ClientTimeout(
            total=request_timeout if request_timeout is not None else 0
        )

        request_headers = normalize_headers(self.headers)
        if headers:
            request_headers.update(normalize_headers(headers))

        if not body:  # Filter out empty bytes
            body = None
        if self._http_compress and body:
            body = gzip.compress(body)
            request_headers["content-encoding"] = "gzip"

        kwargs = {}
        if self._ssl_assert_fingerprint:
            kwargs["ssl"] = aiohttp_fingerprint(self._ssl_assert_fingerprint)

        try:
            start = self._loop.time()
            async with self.session.request(
                method,
                url,
                data=body,
                headers=request_headers,
                timeout=aiohttp_timeout,
                **kwargs,
            ) as response:
                if is_head:  # We actually called 'GET' so throw away the data.
                    await response.release()
                    raw_data = b""
                else:
                    raw_data = await response.read()
                duration = self._loop.time() - start

        # We want to reraise a cancellation or recursion error.
        except RERAISE_EXCEPTIONS:
            raise
        except Exception as e:
            if isinstance(
                e, (asyncio.TimeoutError, aiohttp_exceptions.ServerTimeoutError)
            ):
                raise ConnectionTimeout(
                    "Connection timed out during request", errors=(e,)
                )
            elif isinstance(e, (ssl.SSLError, aiohttp_exceptions.ClientSSLError)):
                raise TlsError(str(e), errors=(e,))
            raise ConnectionError(str(e), errors=(e,))

        return (
            ApiResponseMeta(
                node=self.config,
                duration=duration,
                http_version="1.1",
                status=response.status,
                headers=HttpHeaders(response.headers),
            ),
            raw_data,
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    def _create_aiohttp_session(self) -> None:
        """Creates an aiohttp.ClientSession(). This is delayed until
        the first call to perform_request() so that AsyncTransport has
        a chance to set AiohttpHttpNode.loop
        """
        if self._loop is None:
            self._loop = get_running_loop()
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            skip_auto_headers=("accept", "accept-encoding", "user-agent"),
            auto_decompress=True,
            loop=self._loop,
            cookie_jar=aiohttp.DummyCookieJar(),
            connector=aiohttp.TCPConnector(
                limit=self._connections_per_node,
                use_dns_cache=True,
                ssl=self._ssl_context,
            ),
        )


@functools.lru_cache(maxsize=64, typed=True)
def aiohttp_fingerprint(ssl_assert_fingerprint: str) -> aiohttp.Fingerprint:
    """Changes 'ssl_assert_fingerprint' into a configured 'aiohttp.Fingerprint' instance.
    Uses a cache to prevent creating tons of objects needlessly.
    """
    return aiohttp.Fingerprint(
        base64.b16decode(ssl_assert_fingerprint.replace(":", ""), casefold=True)
    )