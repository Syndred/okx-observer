#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/snapshot_okx_instruments.py

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/download_okx_v2_hourly.py \
  --start 2025-01-01T00:00:00Z --workers 4

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/build_okx_universe_mask.py \
  --data-dir user_data/data/okx_v2_hourly \
  --output user_data/data/okx_v2/universe_top30.feather \
  --report reports/okx-v2/universe-summary.csv \
  --limit 30

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/select_okx_v2_cohort.py

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/download_okx_v2_cohort.py \
  --start 2025-01-01T00:00:00Z --workers 4

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/export_okx_v2_freqtrade_data.py

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py

/Users/syndred/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/report_okx_v2.py
