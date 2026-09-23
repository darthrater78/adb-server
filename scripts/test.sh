#!/usr/bin/env bash
# Runs the test suite. Same command locally and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pytest -q tests "$@"
