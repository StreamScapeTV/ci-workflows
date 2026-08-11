#!/usr/bin/env bash
set -euo pipefail
case "${1:-all}" in
  repository) git diff --check ;;
  all) npm --prefix assets/finance_web ci && flutter analyze --no-pub && flutter test --no-pub ;;
  *) exit 2 ;;
esac
