#!/usr/bin/env bash
set -euo pipefail
id -un
test -x /home/runner/run.sh
docker --version
docker buildx version
docker compose version
docker-compose version
