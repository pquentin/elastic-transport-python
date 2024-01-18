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

"""A command line tool for building releases"""

import os
import shlex

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(argv):
    cmd = " ".join(shlex.quote(x) for x in argv)
    print("$ " + cmd)
    exit_code = os.system(cmd)
    if exit_code != 0:
        print(f"Command exited incorrectly: {exit_code}")
        exit(exit_code)


def main():
    os.chdir(base_dir)
    run(("rm", "-rf", "build/", "dist/", "*.egg-info", ".eggs"))

    # Install and run python-build to create sdist/wheel
    run(("python", "-m", "pip", "install", "-U", "build"))
    run(("python", "-m", "build"))

    # After this run 'python -m twine upload dist/*'
    print(
        "\n\n"
        "===============================\n\n"
        "    * Releases are ready! *\n\n"
        "$ python -m twine upload dist/*\n\n"
        "==============================="
    )


if __name__ == "__main__":
    main()
