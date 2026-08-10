#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

docker compose run --rm --no-deps --entrypoint python freqtrade \
  /freqtrade/scripts/download_binance_archive.py \
  --pairs-file /freqtrade/config/pairs.requested.txt \
  --start 2021-01-01 \
  --end 2026-08-10 \
  --output-dir /freqtrade/user_data/data/binance/futures \
  --report /freqtrade/reports/data-availability.csv \
  "$@"
