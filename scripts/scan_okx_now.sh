#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/scan_okx_trend_compression.py \
  --output-dir reports/okx-screener \
  --compression-atr 2.0 \
  --min-age-days 30

echo "筛选完成：$project_dir/reports/okx-screener/LATEST.md"
