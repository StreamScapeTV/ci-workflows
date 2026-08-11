#!/usr/bin/env bash
set -euo pipefail
flutter pub get --enforce-lockfile
flutter analyze --no-pub
flutter test --no-pub
