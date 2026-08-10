# OKX Shortline V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate one reproducible OKX USDT perpetual 4H/1H/15m trend system for 2–12 hour trades, dynamic top-30 selection, three-position portfolio risk, and honest feasibility reporting.

**Architecture:** Preserve Binance V1 and add an isolated OKX V2 pipeline. Public OKX metadata and 15m candles feed a reproducible hourly universe mask; pure Python modules calculate multi-timeframe signals and portfolio rules; a Freqtrade adapter runs execution-compatible backtests; a walk-forward runner selects parameters before stress and pseudo-holdout evaluation.

**Tech Stack:** Freqtrade 2026.5.1, Docker Compose, Python 3.14 in container, pandas/numpy/Feather, CCXT OKX, unittest, standard-library JSON/CSV/ZIP, Pillow for charts.

## Global Constraints

- Exchange scope is live OKX `USDT`-settled `SWAP` instruments listed for at least 30 full days.
- BTC and ETH are market references only; never trade them.
- Scan all eligible instruments, rank with past data only, and retain 30 per completed hour.
- Base timeframe is 15m; 1H and 4H are resampled only from confirmed 15m candles.
- Maximum 3 open trades, maximum 2 in one direction, default per-trade risk 0.75%, total open risk at most 2%.
- Default leverage is isolated 3x; compare 5x and 10x without treating leverage as alpha.
- Default exit is 40% at 2R, fee-adjusted break-even, 15m EMA20 trail, hard 5R, staged 6H/12H/24H time limits.
- Pass gate is OOS PF ≥ 1.15, drawdown ≤ 35%, at least 300 OOS trades, worst main window PF ≥ 0.95, doubled-cost PF ≥ 1.05.
- If no candidate passes every gate, report V2 as infeasible and do not create a live-start configuration.
- No ADX, RSI, sentiment, order-flow prediction, or post-hoc profitable-coin whitelist.

---

### Task 1: OKX Instrument Snapshot and V2 Configuration

**Files:**
- Create: `scripts/snapshot_okx_instruments.py`
- Create: `user_data/configs/config.okx.v2.json`
- Create: `config/okx_v2_universe.json`
- Test: `tests/test_okx_instruments.py`

**Interfaces:**
- Produces: `normalize_instruments(payload: dict, now_ms: int, min_age_days: int = 30) -> list[dict]` and a timestamped snapshot with `instId`, Freqtrade `symbol`, `instCategory`, `listTime`, `state`, precision, leverage, and eligibility reason.

- [ ] **Step 1: Write failing normalization tests**

```python
def test_only_live_usdt_swaps_older_than_30_days_are_eligible():
    rows = normalize_instruments(sample_payload, now_ms=40 * DAY_MS)
    assert [row["instId"] for row in rows if row["eligible"]] == ["OLD-USDT-SWAP"]

def test_btc_eth_are_reference_only_and_ko_keeps_exchange_category():
    rows = normalize_instruments(sample_payload, now_ms=40 * DAY_MS)
    by_id = {row["instId"]: row for row in rows}
    assert by_id["BTC-USDT-SWAP"]["role"] == "reference"
    assert by_id["KO-USDT-SWAP"]["instCategory"] == "3"
```

- [ ] **Step 2: Run the test and confirm missing-module failure**

Run: `python -m unittest tests.test_okx_instruments -v`  
Expected: import failure for `scripts.snapshot_okx_instruments`.

- [ ] **Step 3: Implement snapshot normalization and retrying public request**

```python
def normalize_instruments(payload: dict, now_ms: int, min_age_days: int = 30) -> list[dict]:
    minimum_age_ms = min_age_days * 86_400_000
    normalized = []
    for item in payload.get("data", []):
        if item.get("instType") != "SWAP" or item.get("settleCcy") != "USDT":
            continue
        list_time = int(item["listTime"])
        eligible = item.get("state") == "live" and now_ms - list_time >= minimum_age_ms
        base = item["instId"].removesuffix("-USDT-SWAP")
        normalized.append({
            "instId": item["instId"],
            "symbol": f"{base}/USDT:USDT",
            "instCategory": item.get("instCategory", ""),
            "listTime": list_time,
            "eligible": eligible and bool(item.get("instCategory")),
            "role": "reference" if base in {"BTC", "ETH"} else "trade",
        })
    return normalized
```

The CLI writes atomically to `config/okx_v2_universe.json`; retries 429/5xx with bounded exponential backoff; records retrieval UTC time and rejected reasons.

- [ ] **Step 4: Add isolated OKX futures configuration**

Set `exchange.name=okx`, `options.defaultType=swap`, `trading_mode=futures`, `margin_mode=isolated`, `timeframe=15m`, `dry_run_wallet=100`, `max_open_trades=3`, and use the generated eligible symbols as static download/backtest scope.

- [ ] **Step 5: Verify tests and exchange discovery**

Run: `python -m unittest tests.test_okx_instruments -v`  
Run: `./scripts/ft.sh list-markets --exchange okx --trading-mode futures --quote USDT --print-csv --no-color`  
Expected: tests pass and OKX futures markets are returned.

- [ ] **Step 6: Commit**

```bash
git add scripts/snapshot_okx_instruments.py user_data/configs/config.okx.v2.json config/okx_v2_universe.json tests/test_okx_instruments.py
git commit -m "feat: snapshot eligible OKX perpetual instruments"
```

### Task 2: Confirmed-Candle Import and Multi-Timeframe Resampling

**Files:**
- Create: `scripts/download_okx_v2_data.py`
- Create: `user_data/strategy_lib/okx_candles.py`
- Test: `tests/test_okx_candles.py`

**Interfaces:**
- Consumes: eligible symbols from `config/okx_v2_universe.json`.
- Produces: `confirmed_candles(rows: list[list[str]]) -> DataFrame`, `resample_confirmed(frame: DataFrame, rule: str) -> DataFrame`, and Feather files under `user_data/data/okx_v2/`.

- [ ] **Step 1: Write failing candle tests**

```python
def test_unconfirmed_last_candle_is_removed():
    assert len(confirmed_candles([confirmed_row, open_row])) == 1

def test_resampling_requires_every_15m_child():
    frame = three_of_four_quarter_hours()
    assert resample_confirmed(frame, "1h").empty
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_okx_candles -v`  
Expected: missing `okx_candles` module.

- [ ] **Step 3: Implement confirmed parsing, deduplication, and resampling**

```python
def resample_confirmed(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    expected = {"1h": 4, "4h": 16}[rule]
    groups = frame.set_index("date").resample(rule, label="left", closed="left")
    output = groups.agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "confirm": "sum",
    })
    return output.loc[output["confirm"] == expected].reset_index()
```

- [ ] **Step 4: Implement paginated downloader with checkpoints**

Download from the later of the requested start date and contract `listTime`; save per-symbol cursors; store raw 15m, mark, and funding files; emit `reports/okx-v2/data-availability.csv` with gaps and actual range.

- [ ] **Step 5: Run tests and a two-symbol smoke import**

Run: `python -m unittest tests.test_okx_candles -v`  
Run: `python scripts/download_okx_v2_data.py --symbols XRP-USDT-SWAP BLEND-USDT-SWAP --start 2026-05-01 --smoke`  
Expected: confirmed 15m plus derived 1h/4h files and no duplicate timestamps.

- [ ] **Step 6: Commit**

```bash
git add scripts/download_okx_v2_data.py user_data/strategy_lib/okx_candles.py tests/test_okx_candles.py reports/okx-v2/data-availability.csv
git commit -m "feat: import confirmed OKX multi-timeframe data"
```

### Task 3: Historical Dynamic Top-30 Universe

**Files:**
- Create: `user_data/strategy_lib/universe_ranker.py`
- Create: `scripts/build_okx_universe_mask.py`
- Test: `tests/test_universe_ranker.py`

**Interfaces:**
- Produces: `rank_hour(snapshot: dict[str, PairFeatures], limit: int = 30) -> list[str]` and `build_universe_mask(frames: dict[str, DataFrame]) -> DataFrame` with `date`, `pair`, `rank`, `eligible`.

- [ ] **Step 1: Write future-safety and ranking tests**

```python
def test_rank_rejects_missing_and_extreme_pairs():
    assert rank_hour(features, limit=2) == ["LIQUID", "BALANCED"]

def test_appending_future_rows_does_not_change_old_ranks():
    assert old_mask.equals(mask_with_future.loc[old_mask.index])
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_universe_ranker -v`  
Expected: missing ranker module.

- [ ] **Step 3: Implement lagged 24H features and deterministic ranking**

Use only data ending at the previous completed hour. Reject insufficient bars, zero-volume ratio failures, missing candles, and volatility outside configured bounds. Break equal scores by pair name for reproducibility.

- [ ] **Step 4: Build and store the mask**

Run: `python scripts/build_okx_universe_mask.py --config user_data/configs/config.okx.v2.json --limit 30`  
Expected: `user_data/data/okx_v2/universe_top30.feather` plus summary CSV.

- [ ] **Step 5: Commit**

```bash
git add user_data/strategy_lib/universe_ranker.py scripts/build_okx_universe_mask.py tests/test_universe_ranker.py reports/okx-v2/universe-summary.csv
git commit -m "feat: rank historical OKX top-30 universe"
```

### Task 4: Pure Multi-Timeframe V2 Signal Engine

**Files:**
- Create: `user_data/strategy_lib/v2_signal_engine.py`
- Test: `tests/test_v2_signal_engine.py`

**Interfaces:**
- Produces: `V2Parameters`, `add_v2_indicators(frame, timeframe)`, `market_regime(btc4h, eth4h)`, and `scan_v2_setups(candles15m, candles1h, candles4h, market4h, universe_mask, params) -> DataFrame`.

- [ ] **Step 1: Write failing long, short, category, timeout, and prefix tests**

```python
def test_crypto_long_requires_own_trend_and_non_opposing_market():
    result = scan_v2_setups(**crypto_long_fixture(eth_regime="neutral"))
    assert result["enter_long"].sum() == 1

def test_equity_contract_ignores_btc_eth_regime():
    result = scan_v2_setups(**equity_long_fixture(btc_regime="short"))
    assert result["enter_long"].sum() == 1

def test_breakout_candle_cannot_be_pullback():
    result = scan_v2_setups(**same_candle_breakout_fixture())
    assert result["enter_long"].sum() == 0

def test_first_15m_pullback_enters_and_later_pullbacks_do_not():
    result = scan_v2_setups(**two_pullback_fixture())
    assert list(result.index[result["enter_long"] == 1]) == [FIRST_PULLBACK_INDEX]

def test_future_append_does_not_change_historical_signals():
    old = scan_v2_setups(**prefix_fixture())
    extended = scan_v2_setups(**prefix_with_future_fixture())
    assert old[["enter_long", "enter_short"]].equals(
        extended.loc[old.index, ["enter_long", "enter_short"]]
    )
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_v2_signal_engine -v`  
Expected: missing engine module.

- [ ] **Step 3: Implement ATR-normalized 4H/1H/15m engine**

```python
@dataclass(frozen=True)
class V2Parameters:
    compression_atr: float = 0.6
    breakout_atr: float = 0.1
    pullback_atr: float = 0.2
    pullback_wait_15m: int = 12
    strict_market_consensus: bool = False
```

Use backward as-of merges whose informative timestamp is shifted to its close time. Preserve the 1H cluster stop in signal columns and reject pairs outside the hourly mask.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_v2_signal_engine -v`  
Expected: all signal and prefix invariance tests pass.

- [ ] **Step 5: Commit**

```bash
git add user_data/strategy_lib/v2_signal_engine.py tests/test_v2_signal_engine.py
git commit -m "feat: add OKX V2 multi-timeframe signal engine"
```

### Task 5: Portfolio Risk and Exit State

**Files:**
- Create: `user_data/strategy_lib/v2_portfolio.py`
- Create: `user_data/strategies/OKX_Shortline_V2.py`
- Test: `tests/test_v2_portfolio.py`
- Test: `tests/test_okx_v2_strategy.py`

**Interfaces:**
- Produces: `risk_budget(open_risks, open_sides, new_side, equity_drawdown) -> float`, immutable `TradeState`, `exit_decision(trade_state: TradeState) -> ExitAction`, and Freqtrade strategy `OKX_Shortline_V2`.

- [ ] **Step 1: Write failing portfolio tests**

```python
def test_third_trade_receives_only_remaining_half_percent():
    assert risk_budget([0.0075, 0.0075], ["long", "short"], "long", 0) == 0.005

def test_third_same_direction_trade_is_rejected():
    assert risk_budget([0.0075, 0.0075], ["long", "long"], "long", 0) == 0

def test_risk_reduces_after_twenty_percent_drawdown():
    assert risk_budget([], [], "long", equity_drawdown=0.20) == 0.005

def test_entry_stops_after_thirty_percent_drawdown():
    assert risk_budget([], [], "long", equity_drawdown=0.30) == 0
```

- [ ] **Step 2: Write failing exit tests**

```python
def test_two_r_reduces_forty_percent_and_moves_to_break_even():
    action = exit_decision(TradeState(age_hours=3, r_multiple=2.1, partial_taken=False,
                                      rate=102, entry=100, ema20=101, fees_ratio=0.001))
    assert action.reduce_fraction == 0.40
    assert action.new_stop >= 100.1

def test_six_hour_no_progress_exit():
    action = exit_decision(TradeState(age_hours=6, r_multiple=0.4, partial_taken=False,
                                      rate=100.4, entry=100, ema20=100, fees_ratio=0.001))
    assert action.full_exit_reason == "no_progress_6h"

def test_runner_exits_at_five_r_or_twenty_four_hours():
    at_target = exit_decision(TradeState(age_hours=8, r_multiple=5.0, partial_taken=True,
                                         rate=105, entry=100, ema20=104, fees_ratio=0.001))
    expired = exit_decision(TradeState(age_hours=24, r_multiple=2.5, partial_taken=True,
                                       rate=102.5, entry=100, ema20=102, fees_ratio=0.001))
    assert at_target.full_exit_reason == "hard_5r"
    assert expired.full_exit_reason == "runner_24h"
```

- [ ] **Step 3: Verify failure**

Run: `python -m unittest tests.test_v2_portfolio tests.test_okx_v2_strategy -v`  
Expected: missing portfolio and strategy modules.

- [ ] **Step 4: Implement pure portfolio rules then Freqtrade callbacks**

The strategy uses `adjust_trade_position`, `custom_stoploss`, `custom_roi`, `custom_exit`, `confirm_trade_entry`, `leverage`, and `custom_stake_amount`. Encode immutable initial stop/risk in the entry tag; never increase risk to satisfy minimum stake.

- [ ] **Step 5: Verify strategy loading and tests**

Run: `python -m unittest tests.test_v2_portfolio tests.test_okx_v2_strategy -v`  
Run: `./scripts/ft.sh list-strategies --config /freqtrade/user_data/configs/config.okx.v2.json --no-color`  
Expected: tests pass and `OKX_Shortline_V2` status is `OK`.

- [ ] **Step 6: Commit**

```bash
git add user_data/strategy_lib/v2_portfolio.py user_data/strategies/OKX_Shortline_V2.py tests/test_v2_portfolio.py tests/test_okx_v2_strategy.py
git commit -m "feat: enforce V2 portfolio risk and exits"
```

### Task 6: Walk-Forward Selection, Stress, and Compounding

**Files:**
- Create: `scripts/run_okx_v2_research.py`
- Create: `user_data/strategy_lib/walk_forward.py`
- Test: `tests/test_walk_forward.py`

**Interfaces:**
- Produces: `rolling_windows(start, end, train_months=12, validation_months=3, step_months=3)`, `candidate_score(window_metrics)`, and research artifacts under `user_data/backtest_results/okx-v2/`.

- [ ] **Step 1: Write failing isolation and hard-gate tests**

```python
def test_windows_never_overlap_training_with_validation():
    for window in rolling_windows(date(2023, 1, 1), date(2026, 1, 1)):
        assert window.train_end <= window.validation_start

def test_high_return_candidate_fails_when_drawdown_exceeds_35_percent():
    metrics = [{"pf": 1.40, "drawdown": 0.36, "trades": 500, "stress_pf": 1.20}]
    assert candidate_score(metrics).passed is False

def test_candidate_requires_double_cost_pf_at_least_1_05():
    metrics = [{"pf": 1.20, "drawdown": 0.20, "trades": 500, "stress_pf": 1.04}]
    assert candidate_score(metrics).passed is False
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_walk_forward -v`  
Expected: missing walk-forward module.

- [ ] **Step 3: Implement deterministic candidate enumeration and scoring**

Enumerate the exact design spaces, store every command/parameter/result, select only from training and validation windows, then freeze before pseudo-holdout. Run risk/leverage and pure/capped compounding only after signal selection.

- [ ] **Step 4: Add doubled-cost stress and pass/fail manifest**

Output `selected-candidate.json` only when every gate passes; otherwise output `research-failed.json` with failed gates and no live config.

- [ ] **Step 5: Run tests and a two-pair smoke research cycle**

Run: `python -m unittest tests.test_walk_forward -v`  
Run: `python scripts/run_okx_v2_research.py --smoke --pairs XRP/USDT:USDT BLEND/USDT:USDT`  
Expected: window artifacts, deterministic selected/failed manifest, and no overlap.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_okx_v2_research.py user_data/strategy_lib/walk_forward.py tests/test_walk_forward.py
git commit -m "feat: add V2 walk-forward research pipeline"
```

### Task 7: Full Data Run and Final Feasibility Report

**Files:**
- Create: `scripts/report_okx_v2.py`
- Create: `reports/okx-v2/FINAL_REPORT.md`
- Create: `reports/okx-v2/metrics.csv`
- Create: `reports/okx-v2/equity.csv`
- Create: `reports/okx-v2/equity.png`
- Modify: `PROJECT_PROGRESS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: all raw window, stress, risk, and compounding artifacts.
- Produces: one final candidate or an explicit infeasible verdict with every requested metric.

- [ ] **Step 1: Download the eligible historical universe and build masks**

Run snapshot, downloader, and universe builder. Preserve all raw data locally and report actual listing-limited ranges.

- [ ] **Step 2: Execute the complete walk-forward and option comparison**

Run the parameter, exit, time, risk, leverage, pure-compounding, and capped-risk comparisons. Do not inspect pseudo-holdout until selection is frozen.

- [ ] **Step 3: Generate final artifacts**

Include trades, long/short split, win rate, average win/loss, payoff, PF, drawdown, maximum consecutive losses, minimum/maximum balance, category/pair contribution, fee/funding/slippage, threshold crossings, and equity curves.

- [ ] **Step 4: Run final verification**

Run: `docker compose config --quiet`  
Run: `docker compose run --rm --no-deps --entrypoint python freqtrade -m unittest discover -s tests -v`  
Run: Freqtrade `lookahead-analysis` for the frozen strategy.  
Run: current-default short backtest.  
Expected: all tests green, strategy `OK`, no lookahead findings, report totals match raw ZIP.

- [ ] **Step 5: Review and commit**

Check `git diff --check`, inspect the equity PNG, verify no secrets or API keys, update Chinese handoff, then commit:

```bash
git add reports/okx-v2 README.md PROJECT_PROGRESS.md scripts/report_okx_v2.py
git commit -m "docs: report OKX V2 feasibility"
```
