#!/usr/bin/env python3
"""Scan current OKX USDT swaps with the causal six-MA three-stage model."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd

from scripts.download_okx_v2_data import request_json
from scripts.bitget_contracts import fetch_bitget_contracts
from scripts.snapshot_okx_instruments import fetch_instruments, normalize_instruments
from user_data.strategy_lib.okx_candles import confirmed_candles
from user_data.strategy_lib.okx_momentum_screener import momentum_snapshot
from user_data.strategy_lib.six_ma_three_stage_screener import (
    ThreeStageParameters,
    odds_field_defaults,
    scan_three_stage_pair,
)
from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators


STAGE_ORDER = {
    "entry_confirmed": 0,
    "wait_first_pullback": 1,
    "mature_15m_coil": 2,
    "forming_15m_coil": 3,
    # Keep old report snapshots readable while all new scans use the layered
    # state names emitted by the Task 1 signal engine.
    "ready_4h_breakout_15m_coiled": 2,
    "watch_both_coiled": 3,
    "none": 4,
}

FORMAL_STAGES = frozenset({"entry_confirmed", "wait_first_pullback"})
OBSERVATION_STAGES = frozenset(
    {
        "mature_15m_coil",
        "forming_15m_coil",
        "ready_4h_breakout_15m_coiled",
        "watch_both_coiled",
    }
)
MATURE_OBSERVATION_STAGES = frozenset(
    {"mature_15m_coil", "ready_4h_breakout_15m_coiled"}
)
FORMING_OBSERVATION_STAGES = frozenset(
    {"forming_15m_coil", "watch_both_coiled"}
)
ACTIONABLE_STAGES = FORMAL_STAGES | OBSERVATION_STAGES

MARKET_REFERENCE_BASES = frozenset({"BTC", "ETH"})
STABLECOIN_BASES = frozenset(
    {
        "BUSD",
        "CRVUSD",
        "DAI",
        "FDUSD",
        "FRAX",
        "GHO",
        "GUSD",
        "LUSD",
        "PYUSD",
        "RLUSD",
        "SUSD",
        "TUSD",
        "USDB",
        "USDC",
        "USDD",
        "USDE",
        "USDK",
        "USD0",
        "USDP",
        "USDS",
        "USDT",
        "USD1",
        "USDG",
        "USTC",
        "EURC",
        "EURT",
    }
)


class RequestLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1 / requests_per_second
        self.lock = threading.Lock()
        self.last_started = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            earliest = self.last_started + self.interval
            if now < earliest:
                delay = earliest - now
                self.last_started = earliest
            else:
                delay = 0.0
                self.last_started = now
        if delay > 0:
            time.sleep(delay)


def fetch_candles(
    instrument_id: str,
    bar: str,
    limit: int,
    limiter: RequestLimiter,
) -> pd.DataFrame:
    limiter.wait()
    payload = request_json(
        "/api/v5/market/candles",
        {"instId": instrument_id, "bar": bar, "limit": limit},
    )
    return confirmed_candles(payload.get("data", []))


def fetch_stage(
    instruments: list[str],
    bar: str,
    limit: int,
    workers: int,
    limiter: RequestLimiter,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}

    def fetch(instrument_id: str) -> tuple[str, pd.DataFrame, str | None]:
        try:
            frame = fetch_candles(instrument_id, bar, limit, limiter)
            return instrument_id, frame, None
        except Exception as error:  # Partial results are more useful than a lost scan.
            return instrument_id, pd.DataFrame(), f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for instrument_id, frame, error in pool.map(fetch, instruments):
            frames[instrument_id] = frame
            if error:
                errors[instrument_id] = error
    return frames, errors


def sort_results(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        return results.copy()
    output = results.copy()
    stage = output["stage"].astype(str)
    output["_stage_priority"] = (
        _numeric_series(output, "stage_priority")
        if "stage_priority" in output
        else stage.map(STAGE_ORDER).fillna(99)
    )
    output["_15m_state_quality"] = _first_numeric_series(
        output,
        "15m_state_quality",
        "fifteen_minute_state_quality",
        "fifteen_minute_coil_score",
    )
    output["_4h_context_score"] = _numeric_series(
        output, "four_hour_context_score"
    )
    output["_daily_alignment_bonus"] = (
        _numeric_series(output, "daily_alignment_bonus")
        if "daily_alignment_bonus" in output
        else _daily_alignment_bonus(output)
    )
    output["_setup_freshness"] = (
        _numeric_series(output, "setup_freshness")
        if "setup_freshness" in output
        else _setup_freshness(output)
    )
    output = output.sort_values(
        [
            "_stage_priority",
            "_15m_state_quality",
            "_4h_context_score",
            "_daily_alignment_bonus",
            "_setup_freshness",
            "instrument",
        ],
        ascending=[True, False, False, False, False, True],
        kind="stable",
    )
    return output.drop(
        columns=[
            "_stage_priority",
            "_15m_state_quality",
            "_4h_context_score",
            "_daily_alignment_bonus",
            "_setup_freshness",
        ]
    ).reset_index(drop=True)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric sort series even when a diagnostic column is absent."""

    if column not in frame:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _first_numeric_series(frame: pd.DataFrame, *columns: str) -> pd.Series:
    for column in columns:
        if column in frame:
            return _numeric_series(frame, column)
    return pd.Series(0.0, index=frame.index, dtype=float)


def _daily_alignment_bonus(frame: pd.DataFrame) -> pd.Series:
    direction = (
        frame["direction"].astype(str)
        if "direction" in frame
        else pd.Series("none", index=frame.index)
    )
    daily = (
        frame["daily_bias"].astype(str)
        if "daily_bias" in frame
        else pd.Series("neutral", index=frame.index)
    )
    return pd.Series(
        np.select(
            [
                direction.isin({"long", "short"}) & (direction == daily),
                direction.isin({"long", "short"})
                & daily.isin({"long", "short"})
                & (direction != daily),
            ],
            [1.0, -1.0],
            default=0.0,
        ),
        index=frame.index,
        dtype=float,
    )


def _setup_freshness(frame: pd.DataFrame) -> pd.Series:
    """Use the latest lifecycle timestamp as a deterministic freshness key."""

    timestamps = []
    for column in (
        "next_executable_at",
        "first_pullback_at",
        "fifteen_minute_breakout_at",
        "four_hour_breakout_at",
    ):
        if column in frame:
            timestamps.append(pd.to_datetime(frame[column], utc=True, errors="coerce"))
    if not timestamps:
        return pd.Series(float("-inf"), index=frame.index, dtype=float)
    values = pd.concat(timestamps, axis=1).max(axis=1)
    return values.astype("int64").where(values.notna(), -1).astype(float)


def observation_rows(
    results: pd.DataFrame, params: ThreeStageParameters | None = None
) -> pd.DataFrame:
    """Return positive-score mature/forming observations under the configured cap."""

    if results.empty or "stage" not in results:
        return results.copy()
    active = params or ThreeStageParameters()
    candidates = results.loc[results["stage"].isin(OBSERVATION_STAGES)].copy()
    candidates = candidates.loc[
        _first_numeric_series(
            candidates,
            "fifteen_minute_coil_score",
            "15m_state_quality",
            "fifteen_minute_state_quality",
        )
        > 0
    ]
    return sort_results(candidates).head(active.max_observation_items).reset_index(drop=True)


def actionable_count(results: pd.DataFrame) -> int:
    """Count formal and layered observation states for the CLI summary."""

    if results.empty or "stage" not in results:
        return 0
    return int(results["stage"].isin(ACTIONABLE_STAGES).sum())


def build_scanner_universe(
    instruments: list[dict[str, object]],
    bitget_symbols: set[str],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Annotate live OKX metadata with the scanner's crypto/Bitget universe rules."""

    available = {str(symbol).upper() for symbol in bitget_symbols}
    exclusions: Counter[str] = Counter()
    output: list[dict[str, object]] = []
    for source in instruments:
        row = dict(source)
        instrument_id = str(row.get("instId", ""))
        base = instrument_id.removesuffix("-USDT-SWAP").upper()
        category = str(row.get("instCategory", ""))
        market_reference = base in MARKET_REFERENCE_BASES
        symbol = f"{base}USDT"
        bitget_available = symbol in available
        if not bool(row.get("eligible")):
            reason = str(row.get("eligibility_reason") or "not_eligible")
        elif category != "1":
            reason = "non_crypto_category"
        elif base in STABLECOIN_BASES:
            reason = "stablecoin_base"
        elif not bitget_available:
            reason = "bitget_unavailable"
        else:
            reason = "eligible"
        scanner_eligible = reason == "eligible"
        if not scanner_eligible:
            exclusions[reason] += 1
        row.update(
            {
                "role": "trade" if market_reference else str(row.get("role", "trade")),
                "market_reference": market_reference,
                "bitget_available": bitget_available,
                "scanner_eligible": scanner_eligible,
                "scanner_exclusion_reason": None if scanner_eligible else reason,
            }
        )
        output.append(row)
    return output, dict(exclusions)


def _fmt(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.{digits}f}" if np.isfinite(number) else "-"


def _time(value: object) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "-"
    try:
        if pd.isna(value):
            return "-"
        return pd.Timestamp(value).isoformat()
    except (TypeError, ValueError):
        return "-"


def _change_pct(
    source: pd.DataFrame,
    close: float,
    latest_date: pd.Timestamp,
    lookback: pd.Timedelta,
) -> float:
    """Percent change versus the last completed close at or before lookback."""

    if not np.isfinite(close) or source.empty:
        return np.nan
    prior = source.loc[source["date"] <= latest_date - lookback]
    if prior.empty:
        return np.nan
    try:
        reference = float(prior.iloc[-1]["close"])
    except (TypeError, ValueError, IndexError):
        return np.nan
    if not np.isfinite(reference) or reference <= 0:
        return np.nan
    return (close / reference - 1.0) * 100.0


def _window_diagnostics(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    interval: pd.Timedelta,
) -> dict[str, object]:
    """Describe the latest completed candle without affecting signal logic."""

    empty = {
        "window_end": pd.NaT,
        "current_close": np.nan,
        "current_close_vs_six_lines": "unknown",
        "change_24h_pct": np.nan,
    }
    if frame.empty or not {"date", "close"}.issubset(frame.columns):
        return empty
    try:
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True, errors="coerce")
        source = source.dropna(subset=["date"])
        cutoff = pd.Timestamp(as_of)
        cutoff = (
            cutoff.tz_localize("UTC")
            if cutoff.tzinfo is None
            else cutoff.tz_convert("UTC")
        )
        source = source.loc[source["date"] + interval <= cutoff]
        if source.empty:
            return empty
        source = add_v2_indicators(source)
        latest = source.iloc[-1]
        close = float(latest["close"])
        latest_date = pd.Timestamp(latest["date"])
        change_24h_pct = _change_pct(source, close, latest_date, pd.Timedelta(hours=24))
        averages = pd.to_numeric(latest.loc[list(SIX_AVERAGES)], errors="coerce")
        relation = "unknown"
        if np.isfinite(close) and averages.notna().all():
            if close > float(averages.max()):
                relation = "above_all"
            elif close < float(averages.min()):
                relation = "below_all"
            else:
                relation = "inside_six_line_band"
        return {
            "window_end": latest_date + interval,
            "current_close": close,
            "current_close_vs_six_lines": relation,
            "change_24h_pct": change_24h_pct,
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return empty


def _stage_table(results: pd.DataFrame, stage: str) -> str:
    if results.empty or "stage" not in results or "role" not in results:
        return "当前没有符合该阶段的合约。"
    stage_aliases = {
        "mature_15m_coil": MATURE_OBSERVATION_STAGES,
        "forming_15m_coil": FORMING_OBSERVATION_STAGES,
        "entry_confirmed": frozenset({"entry_confirmed"}),
        "wait_first_pullback": frozenset({"wait_first_pullback"}),
    }
    aliases = stage_aliases.get(stage, frozenset({stage}))
    frame = results.loc[
        results["stage"].isin(aliases) & (results["role"] == "trade")
    ]
    if frame.empty:
        return "当前没有符合该阶段的合约。"
    lines = [
        "| 合约 | 方向 | 玩法 | 计划赔率 | 目标 | Bitget可用 | 15m状态/分数 | 收缩/压缩 | 4H上下文/分数 | 风险 | 日线偏向 | 当前收盘相对六线 | 15m窗口末端 | 4H窗口末端 | 4H突破 | 15m突破 | 首次回踩 | 下一可执行时间 | 止损 | 无效原因 |",
        "|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---:|---|",
    ]
    for _, row in frame.iterrows():
        direction = {"long": "多", "short": "空", "none": "待定"}.get(
            str(row["direction"]), str(row["direction"])
        )
        risk = row.get("risk_labels", [])
        if isinstance(risk, (list, tuple, set)):
            risk_text = ", ".join(str(item) for item in risk) or "-"
        elif pd.isna(risk):
            risk_text = "-"
        else:
            risk_text = str(risk)
        lines.append(
            f"| {row['instrument']} | {direction} | "
            f"{row.get('playbook', '-')} | "
            f"{_fmt(row.get('planned_rr'), 2)} | "
            f"{_fmt(row.get('target_price'), 8)} | "
            f"{'是' if bool(row.get('bitget_available')) else '否'} | "
            f"{row.get('fifteen_minute_state', '-')} / "
            f"{_fmt(row.get('fifteen_minute_coil_score'), 1)} | "
            f"{_fmt(row.get('fifteen_minute_contraction_ratio'), 3)} / "
            f"{_fmt(row.get('fifteen_minute_current_compression_atr'), 2)} ATR | "
            f"{row.get('four_hour_context', '-')} / "
            f"{_fmt(row.get('four_hour_context_score'), 1)} | "
            f"{risk_text} | {row.get('daily_bias', 'neutral')} | "
            f"{row.get('current_close_vs_six_lines', 'unknown')} | "
            f"{_time(row.get('fifteen_minute_window_end'))} | "
            f"{_time(row.get('four_hour_window_end'))} | "
            f"{_time(row.get('four_hour_breakout_at'))} | "
            f"{_time(row.get('fifteen_minute_breakout_at'))} | "
            f"{_time(row.get('first_pullback_at'))} | "
            f"{_time(row.get('next_executable_at'))} | "
            f"{_fmt(row.get('initial_stop_price'), 8)} | "
            f"{row.get('reason', '-')} |"
        )
    return "\n".join(lines)


MOMENTUM_LAUNCHED = frozenset(
    {
        "momentum_up",
        "momentum_down",
        "volume_spike_up",
        "volume_spike_down",
        "four_hour_up",
        "four_hour_down",
    }
)
MOMENTUM_WATCH = frozenset({"watch_up", "watch_down"})
MOMENTUM_LABELS = {
    "momentum_up": "连续上涨且放量",
    "momentum_down": "连续下跌且放量",
    "volume_spike_up": "首日放量上涨",
    "volume_spike_down": "首日放量下跌",
    "four_hour_up": "4H已启动上涨",
    "four_hour_down": "4H已启动下跌",
    "watch_up": "连涨、量能未放大",
    "watch_down": "连跌、量能未放大",
}


def _momentum_table(results: pd.DataFrame) -> str:
    if results.empty or "momentum_status" not in results:
        return "当前没有连续涨跌或放量合约。"
    trade = results.loc[results["role"] == "trade"] if "role" in results else results
    frame = trade.loc[trade["momentum_status"].isin(MOMENTUM_LAUNCHED | MOMENTUM_WATCH)]
    if frame.empty:
        return "当前没有连续涨跌或放量合约。"
    lines = [
        "| 合约 | 状态 | 方向 | 日线连续 | 4H连续 | 量能倍数 | 振幅/ATR | 24h |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.head(24).iterrows():
        direction = {"long": "多", "short": "空"}.get(str(row.get("direction")), "待定")
        status = str(row.get("momentum_status") or "")
        lines.append(
            f"| {row['instrument']} | {MOMENTUM_LABELS.get(status, status)} | "
            f"{direction} | {int(row.get('daily_streak') or 0)} | "
            f"{int(row.get('four_hour_streak') or 0)} | "
            f"{_fmt(row.get('volume_ratio'))} | {_fmt(row.get('atr_ratio'))} | "
            f"{_fmt(row.get('change_24h_pct'), 2)}% |"
        )
    return "\n".join(lines)


def _near_stage_table(results: pd.DataFrame) -> str:
    del results
    return "当前没有可展示的部分阶段观察项。"


def markdown_report(
    results: pd.DataFrame,
    retrieved_at: datetime,
    params: ThreeStageParameters,
    total_live: int,
    errors: dict[str, str],
    eligible_trade_count: int | None = None,
    scanner_exclusions: dict[str, int] | None = None,
) -> str:
    ordered = sort_results(results)
    if "role" in ordered and "stage" in ordered:
        trade = ordered.loc[ordered["role"] == "trade"]
        counts = trade["stage"].value_counts()
    else:
        trade = pd.DataFrame(columns=["stage"])
        counts = pd.Series(dtype="int64")
    observations = observation_rows(ordered, params)
    scanner_exclusions = scanner_exclusions or {}
    exclusion_text = "、".join(
        f"{key} {value}" for key, value in sorted(scanner_exclusions.items())
    ) or "无"
    eligible_text = "未知" if eligible_trade_count is None else str(eligible_trade_count)
    mature_count = int(trade["stage"].isin(MATURE_OBSERVATION_STAGES).sum())
    forming_count = int(trade["stage"].isin(FORMING_OBSERVATION_STAGES).sum())
    return f"""# OKX 六均线四层机会筛选器

更新时间：{retrieved_at.astimezone().isoformat()}

## 规则摘要

- 高赔率打法：4H 已发散且同向后，15m 第一次回踩 20 均线；止损贴 20 均线，目标取前方最近的 4H/日线密集区中心。计划赔率 < 2R 不进入优先查看。
- 全部信号只使用已收盘K线，核心指标只含 MA20/60/120、EMA20/60/120 与 ATR 标准化。
- 采用 `15m-first`：先识别15m成熟缠绕或正在收拢，再用4H/日线作为高周期上下文，而不是硬删除机会。
- 高周期上下文可为4H成熟/形成、4H与日线趋势同向、仅日线趋势或数据未知；明确反向只阻止最终确认。
- 15m同向突破后只认后续第一次回踩；第一次触碰失守即取消，不等第二次。
- 日线仅作方向偏向，不作为前两阶段硬门槛；BTC/ETH市场过滤只作用于加密类最终确认。
- `entry_confirmed` 仅在下一根15m开盘前有效。本表是人工筛选辅助，不是自动开仓指令，也不是盈利证明。

共检查 {total_live} 个 live USDT 永续；行情请求异常 {len(errors)} 个。当前交易合约状态：确认 {int(counts.get('entry_confirmed', 0))}、等待回踩 {int(counts.get('wait_first_pullback', 0))}、成熟观察 {mature_count}、形成中观察 {forming_count}。

扫描口径：仅 `instCategory=1` 加密类，排除稳定币基准；仅纳入 Bitget 官方 USDT 永续、状态 normal 且未下线的交集合约。BTC/ETH 以交易角色参与，同时保留市场参考标记。扫描合资格交集：{eligible_text}；排除统计：{exclusion_text}。

## 1. 回踩确认

{_stage_table(ordered, 'entry_confirmed')}

## 2. 等待第一次回踩

{_stage_table(ordered, 'wait_first_pullback')}

## 3. 15m成熟缠绕观察

{_stage_table(observations, 'mature_15m_coil')}

## 4. 15m正在收拢观察

{_stage_table(observations, 'forming_15m_coil')}

## 5. 连续涨跌 / 放量异动

{_momentum_table(ordered)}

## 参数口径

{chr(10).join(f"- `{name}` = {value}" for name, value in asdict(params).items())}

观察层合计纳入 {len(observations)} 条（上限 {params.max_observation_items} 条）；诊断 CSV 保留全部交易合约与排除原因。
"""


def build_results(
    instruments: list[dict[str, object]],
    daily: dict[str, pd.DataFrame],
    four_hour: dict[str, pd.DataFrame],
    fifteen: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    params: ThreeStageParameters,
) -> pd.DataFrame:
    btc4h = four_hour.get("BTC-USDT-SWAP", pd.DataFrame())
    eth4h = four_hour.get("ETH-USDT-SWAP", pd.DataFrame())
    rows: list[dict[str, object]] = []
    for instrument in instruments:
        instrument_id = str(instrument["instId"])
        role = str(instrument["role"])
        if not bool(instrument.get("scanner_eligible")) or role != "trade":
            state = {
                "stage": "none",
                "direction": "none",
                "daily_bias": "neutral",
                "daily_direction": "unknown",
                "daily_strong_direction": "unknown",
                "four_hour_state": "none",
                "four_hour_context": "unknown",
                "four_hour_context_score": 0.0,
                "four_hour_context_direction": "neutral",
                "risk_labels": [],
                "four_hour_coil_score": 0.0,
                "four_hour_zone_high": np.nan,
                "four_hour_zone_low": np.nan,
                "four_hour_breakout_at": pd.NaT,
                "fifteen_minute_state": "none",
                "fifteen_minute_coil_score": 0.0,
                "fifteen_minute_current_compression_atr": np.nan,
                "fifteen_minute_compressed_fraction": 0.0,
                "fifteen_minute_trailing_compressed_bars": 0,
                "fifteen_minute_contraction_ratio": 0.0,
                "fifteen_minute_zone_high": np.nan,
                "fifteen_minute_zone_low": np.nan,
                "fifteen_minute_breakout_at": pd.NaT,
                "first_pullback_at": pd.NaT,
                "initial_stop_price": np.nan,
                "next_executable_at": pd.NaT,
                "reason": str(
                    instrument.get("scanner_exclusion_reason")
                    or instrument.get("eligibility_reason")
                    or "not_eligible"
                ),
                **odds_field_defaults(),
            }
        else:
            state = scan_three_stage_pair(
                pair=instrument_id,
                inst_category=str(instrument["instCategory"]),
                candles15m=fifteen.get(instrument_id, pd.DataFrame()),
                candles4h=four_hour.get(instrument_id, pd.DataFrame()),
                candles1d=daily.get(instrument_id, pd.DataFrame()),
                btc4h=btc4h,
                eth4h=eth4h,
                as_of=as_of,
                params=params,
            )
        diagnostic15 = _window_diagnostics(
            fifteen.get(instrument_id, pd.DataFrame()), as_of, pd.Timedelta(minutes=15)
        )
        diagnostic4h = _window_diagnostics(
            four_hour.get(instrument_id, pd.DataFrame()), as_of, pd.Timedelta(hours=4)
        )
        momentum = momentum_snapshot(
            daily.get(instrument_id, pd.DataFrame()),
            four_hour.get(instrument_id, pd.DataFrame()),
        )
        rows.append(
            {
                **state,
                "instrument": instrument_id,
                "role": role,
                "market_reference": bool(instrument.get("market_reference")),
                "bitget_available": bool(instrument.get("bitget_available")),
                "scanner_eligible": bool(instrument.get("scanner_eligible")),
                "scanner_exclusion_reason": instrument.get("scanner_exclusion_reason"),
                "inst_category": instrument["instCategory"],
                "age_days": instrument["ageDays"],
                "list_time": instrument.get("listTime"),
                "list_time_utc": instrument.get("listTimeUtc"),
                "current_close": diagnostic15["current_close"],
                "current_close_vs_six_lines": diagnostic15[
                    "current_close_vs_six_lines"
                ],
                "change_24h_pct": diagnostic15["change_24h_pct"],
                "fifteen_minute_window_end": diagnostic15["window_end"],
                "four_hour_window_end": diagnostic4h["window_end"],
                "momentum_status": momentum["status"],
                "momentum_candidate": momentum["candidate"],
                "daily_streak": momentum["daily_streak"],
                "four_hour_streak": momentum["four_hour_streak"],
                "volume_ratio": momentum["volume_ratio"],
                "atr_ratio": momentum["atr_ratio"],
                "daily_change_pct": momentum["daily_change_pct"],
                "momentum_score": momentum["score"],
            }
        )
    return sort_results(pd.DataFrame(rows))


def build_manifest(
    results: pd.DataFrame,
    retrieved_at: datetime,
    params: ThreeStageParameters,
    total_live: int,
    eligible_trade_count: int,
    scanner_exclusions: dict[str, int],
    errors: dict[str, str],
    min_age_days: int = 0,
) -> dict[str, object]:
    if "role" in results and "stage" in results:
        eligible = (
            results["scanner_eligible"].fillna(False).astype(bool)
            if "scanner_eligible" in results
            else pd.Series(True, index=results.index)
        )
        trade = results.loc[(results["role"] == "trade") & eligible]
        counts = trade["stage"].value_counts()
    else:
        trade = pd.DataFrame(columns=["stage"])
        counts = pd.Series(dtype="int64")
    observation_counts = {
        "mature_15m_coil": int(
            trade["stage"].isin(MATURE_OBSERVATION_STAGES).sum()
        ),
        "forming_15m_coil": int(
            trade["stage"].isin(FORMING_OBSERVATION_STAGES).sum()
        ),
    }
    observation_counts["included"] = len(observation_rows(results, params))
    observation_counts["cap"] = int(params.max_observation_items)
    return {
        "retrievedAt": retrieved_at.isoformat(),
        "minAgeDays": int(min_age_days),
        "parameters": asdict(params),
        "liveUsdtSwaps": total_live,
        "eligibleTradeContracts": eligible_trade_count,
        "stageCounts": {str(key): int(value) for key, value in counts.items()},
        "observationCounts": observation_counts,
        "scannerExclusionCounts": dict(scanner_exclusions),
        "scannerUniverse": {
            "instCategory": "1",
            "stablecoinBasesExcluded": sorted(STABLECOIN_BASES),
            "bitgetContractIntersection": True,
            "marketReferenceBases": sorted(MARKET_REFERENCE_BASES),
        },
        "errors": errors,
        "claimBoundary": "current causal screening snapshot; not a profitability result",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-age-days", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--requests-per-second", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/okx-three-stage-screener"),
    )
    args = parser.parse_args()
    if (
        not 1 <= args.workers <= 12
        or args.requests_per_second <= 0
        or args.min_age_days < 0
    ):
        raise SystemExit("invalid workers or requests-per-second")
    if args.limit < 136 or args.limit > 300:
        raise SystemExit("limit must be between 136 and 300")

    params = ThreeStageParameters()
    retrieved_at = datetime.now(timezone.utc)
    normalized = normalize_instruments(
        fetch_instruments(),
        now_ms=int(retrieved_at.timestamp() * 1000),
        min_age_days=args.min_age_days,
    )
    live = [row for row in normalized if row["state"] == "live"]
    bitget_symbols = fetch_bitget_contracts()
    scanner_instruments, scanner_exclusions = build_scanner_universe(
        live, bitget_symbols
    )
    eligible_trade = [
        row
        for row in scanner_instruments
        if row["role"] == "trade" and row["scanner_eligible"]
    ]
    trade_ids = [str(row["instId"]) for row in eligible_trade]
    reference_ids = [
        str(row["instId"])
        for row in scanner_instruments
        if row.get("market_reference") and row["scanner_eligible"]
    ]
    limiter = RequestLimiter(args.requests_per_second)

    print(f"[1/3] downloading daily candles for {len(trade_ids)} contracts", flush=True)
    daily, errors1d = fetch_stage(
        trade_ids, "1Dutc", args.limit, args.workers, limiter
    )
    print(f"[2/3] downloading 4H candles for {len(trade_ids)} contracts", flush=True)
    four_hour, errors4h = fetch_stage(
        sorted(set(trade_ids + reference_ids)),
        "4H",
        args.limit,
        args.workers,
        limiter,
    )
    print(f"[3/3] downloading 15m candles for {len(trade_ids)} contracts", flush=True)
    fifteen, errors15m = fetch_stage(
        trade_ids, "15m", args.limit, args.workers, limiter
    )
    errors = {
        **{f"1d:{key}": value for key, value in errors1d.items()},
        **{f"4h:{key}": value for key, value in errors4h.items()},
        **{f"15m:{key}": value for key, value in errors15m.items()},
    }
    results = build_results(
        scanner_instruments,
        daily,
        four_hour,
        fifteen,
        pd.Timestamp(retrieved_at),
        params,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output_dir / "latest.csv", index=False)
    report = markdown_report(
        results,
        retrieved_at,
        params,
        len(live),
        errors,
        eligible_trade_count=len(eligible_trade),
        scanner_exclusions=scanner_exclusions,
    )
    (args.output_dir / "LATEST.md").write_text(report, encoding="utf-8")
    manifest = build_manifest(
        results,
        retrieved_at,
        params,
        len(live),
        len(eligible_trade),
        scanner_exclusions,
        errors,
        min_age_days=args.min_age_days,
    )
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    actionable = actionable_count(results)
    print(f"saved {actionable} active states to {args.output_dir / 'LATEST.md'}")


if __name__ == "__main__":
    main()
