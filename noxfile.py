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

import pathlib
import tempfile

import nox

SOURCE_FILES = (
    "noxfile.py",
    "setup.py",
    "elastic_transport/",
    "utils/",
    "tests/",
    "docs/sphinx/",
)


@nox.session
def check_package(session):
    session.install("build", "pip", "twine")
    with tempfile.TemporaryDirectory() as tmp_dir:
        session.run("python", "-m", "build", "--outdir", tmp_dir)
        session.chdir(tmp_dir)

        for dist in pathlib.Path(tmp_dir).iterdir():
            print("dist", dist)
            session.run("twine", "check", dist)

            # Test out importing from the package
            session.run("pip", "install", dist)
            session.run(
                "python",
                "-c",
                "from elastic_transport import Transport, Urllib3HttpNode, RequestsHttpNode",
            )

            # Uninstall the dist, see that we can't import things anymore
            session.run("pip", "uninstall", "--yes", "elastic-transport")
            session.run(
                "python",
                "-c",
                "from elastic_transport import Transport",
                success_codes=[1],
                silent=True,
            )


@nox.session()
def format(session):
    session.install("black~=23.0", "isort", "pyupgrade")
    session.run("black", "--target-version=py37", *SOURCE_FILES)
    session.run("isort", *SOURCE_FILES)
    session.run("python", "utils/license-headers.py", "fix", *SOURCE_FILES)

    lint(session)


@nox.session
def lint(session):
    session.install(
        "flake8",
        "black~=23.0",
        "isort",
        "mypy==1.7.1",
        "types-requests",
        "types-certifi",
    )
    # https://github.com/python/typeshed/issues/10786
    session.run(
        "python", "-m", "pip", "uninstall", "--yes", "types-urllib3", silent=True
    )
    session.install(".[develop]")
    session.run("black", "--check", "--target-version=py37", *SOURCE_FILES)
    session.run("isort", "--check", *SOURCE_FILES)
    session.run("flake8", "--ignore=E501,W503,E203", *SOURCE_FILES)
    session.run("python", "utils/license-headers.py", "check", *SOURCE_FILES)
    session.run("mypy", "--strict", "--show-error-codes", "elastic_transport/")


@nox.session(python=["3.7", "3.8", "3.9", "3.10", "3.11", "3.12"])
def test(session):
    session.install(".[develop]")
    session.run(
        "pytest",
        "--cov=elastic_transport",
        *(session.posargs or ("tests/",)),
        env={"PYTHONWARNINGS": "always::DeprecationWarning"},
    )
    session.run("coverage", "report", "-m")


@nox.session(python="3")
def docs(session):
    session.install(".[develop]")

    session.chdir("docs/sphinx")
    session.run(
        "sphinx-build",
        "-T",
        "-E",
        "-b",
        "html",
        "-d",
        "_build/doctrees",
        "-D",
        "language=en",
        ".",
        "_build/html",
    )
