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

import gzip
import ssl
import time
import warnings
from typing import Optional, Tuple

import urllib3

from .._exceptions import ConnectionError, ConnectionTimeout, SecurityWarning, TlsError
from .._models import ApiResponseMeta, HttpHeaders, NodeConfig
from ..client_utils import DEFAULT, client_meta_version
from ._base import RERAISE_EXCEPTIONS, BaseNode

try:
    import requests
    from requests.adapters import HTTPAdapter

    _REQUESTS_AVAILABLE = True
    _REQUESTS_META_VERSION = client_meta_version(requests.__version__)
except ImportError:  # pragma: nocover
    _REQUESTS_AVAILABLE = False
    _REQUESTS_META_VERSION = ""


class RequestsHttpNode(BaseNode):
    """
    Connection using the `requests` library communicating via HTTP.

    :arg use_ssl: use ssl for the connection if `True`
    :arg verify_certs: whether to verify SSL certificates
    :arg ssl_show_warn: show warning when verify certs is disabled
    :arg ca_certs: optional path to CA bundle. By default standard requests'
        bundle will be used.
    :arg client_cert: path to the file containing the private key and the
        certificate, or cert only if using client_key
    :arg client_key: path to the file containing the private key if using
        separate cert and key files (client_cert will contain only the cert)
    :arg headers: any custom http headers to be add to requests
    :arg http_compress: Use gzip compression
    :arg opaque_id: Send this value in the 'X-Opaque-Id' HTTP header
        For tracing all requests made by this transport.
    """

    _ELASTIC_CLIENT_META = ("rq", _REQUESTS_META_VERSION)

    def __init__(self, config: NodeConfig):
        if not _REQUESTS_AVAILABLE:  # pragma: nocover
            raise ValueError(
                "You must have 'requests' installed to use RequestsHttpNode"
            )

        super().__init__(config)

        # Initialize Session so .headers works before calling super().__init__().
        self.session = requests.Session()
        self.session.headers.clear()  # Empty out all the default session headers
        self.session.verify = config.verify_certs

        # Client certificates
        if config.client_cert:
            if config.client_key:
                self.session.cert = (config.client_cert, config.client_key)
            else:
                self.session.cert = config.client_cert

        if config.ca_certs:
            if not config.verify_certs:
                raise ValueError(
                    "You cannot pass CA certificates when verify_ssl=False."
                )
            self.session.verify = config.ca_certs

        if not config.ssl_show_warn:
            urllib3.disable_warnings()

        if (
            config.scheme == "https"
            and not config.verify_certs
            and config.ssl_show_warn
        ):
            warnings.warn(
                f"Connecting to {self.base_url!r} using TLS with verify_certs=False is insecure",
                stacklevel=2,
                category=SecurityWarning,
            )

        # Create and mount custom adapter for constraining number of connections
        adapter = _ElasticHTTPAdapter(
            node_config=config,
            pool_connections=config.connections_per_node,
            pool_maxsize=config.connections_per_node,
            pool_block=True,
        )
        for prefix in ("http://", "https://"):
            self.session.mount(prefix=prefix, adapter=adapter)

    def perform_request(
        self,
        method: str,
        target: str,
        body: Optional[bytes] = None,
        request_timeout=DEFAULT,
        ignore_status=(),
        headers=None,
    ) -> Tuple[ApiResponseMeta, bytes]:
        url = self.base_url + target
        headers = HttpHeaders(headers or ())

        if not body:  # Filter out empty bytes
            body = None
        if self._http_compress and body:
            body = gzip.compress(body)
            headers["content-encoding"] = "gzip"

        request_headers = self.headers.copy()
        if headers:
            request_headers.update(headers)

        start = time.time()
        request = requests.Request(
            method=method, headers=request_headers, url=url, data=body
        )
        prepared_request = self.session.prepare_request(request)
        send_kwargs = {
            "timeout": request_timeout
            if request_timeout is not DEFAULT
            else self.config.request_timeout
        }
        send_kwargs.update(
            self.session.merge_environment_settings(
                prepared_request.url, {}, None, None, None
            )
        )
        try:
            response = self.session.send(prepared_request, **send_kwargs)
            data = response.content
            duration = time.time() - start
            response_headers = HttpHeaders(response.headers)

        except RERAISE_EXCEPTIONS:
            raise
        except Exception as e:
            if isinstance(e, requests.Timeout):
                raise ConnectionTimeout(
                    "Connection timed out during request", errors=(e,)
                )
            elif isinstance(e, (ssl.SSLError, requests.exceptions.SSLError)):
                raise TlsError(str(e), errors=(e,))
            raise ConnectionError(str(e), errors=(e,))

        response = ApiResponseMeta(
            node=self.config,
            duration=duration,
            http_version="1.1",
            status=response.status_code,
            headers=response_headers,
        )
        return response, data

    def close(self) -> None:
        """
        Explicitly closes connections
        """
        self.session.close()


class _ElasticHTTPAdapter(HTTPAdapter):
    def __init__(self, node_config: NodeConfig, **kwargs):
        self._node_config = node_config
        super().__init__(**kwargs)

    def init_poolmanager(
        self, connections, maxsize, block=False, **pool_kwargs
    ) -> urllib3.PoolManager:
        if self._node_config.ssl_context:
            pool_kwargs.setdefault("ssl_context", self._node_config.ssl_context)
        if self._node_config.ssl_assert_fingerprint:
            pool_kwargs.setdefault(
                "ssl_assert_fingerprint", self._node_config.ssl_assert_fingerprint
            )
        return super().init_poolmanager(
            connections, maxsize, block=block, **pool_kwargs
        )
