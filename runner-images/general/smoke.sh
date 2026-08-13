#!/usr/bin/env bash
set -euo pipefail
id -un
test -x /home/runner/run.sh
python3 --version
node --version
git --version
jq --version
yq --version
