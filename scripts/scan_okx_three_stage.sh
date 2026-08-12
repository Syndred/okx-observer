#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/scan_okx_three_stage.py "$@"
