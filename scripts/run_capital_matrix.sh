#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
timerange="${1:-20210101-20260101}"
result_root="${2:-/freqtrade/user_data/backtest_results/capital-matrix}"

cd "$project_dir"
mkdir -p reports/logs

for leverage in 3 5 10; do
  for risk_pct in 0.01 0.02 0.05; do
    risk_label="$(printf '%s' "$risk_pct" | tr -d '.')"
    scenario="${leverage}x-risk-${risk_label}"
    echo "Running $scenario on $timerange"
    docker compose run --rm --no-deps \
      -e "V1_LEVERAGE=$leverage" \
      -e "V1_RISK_PCT=$risk_pct" \
      freqtrade backtesting \
      --config /freqtrade/user_data/configs/config.base.json \
      --strategy MA_EMA_Trend_Strategy \
      --timeframe 1h \
      --timerange "$timerange" \
      --cache none \
      --export trades \
      --backtest-directory "$result_root/$scenario" \
      --notes "capital-matrix:$scenario" \
      >"reports/logs/${scenario}.log" 2>&1
  done
done
