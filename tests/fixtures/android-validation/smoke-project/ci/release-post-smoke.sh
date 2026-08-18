#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p build/outputs/release build/reports
printf 'synthetic-apk\n' > build/outputs/release/smoke-release.apk
printf 'synthetic-aab\n' > build/outputs/release/smoke-release.aab
printf '%s\n' '{"policy":"success"}' > build/reports/release-policy-report.json
