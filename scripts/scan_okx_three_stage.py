#!/usr/bin/env python3
"""Scan current OKX USDT swaps with the causal six-MA three-stage model."""

from __future__ import annotations

import argparse
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
from scripts.snapshot_okx_instruments import fetch_instruments, normalize_instruments
from user_data.strategy_lib.okx_candles import confirmed_candles
from user_data.strategy_lib.six_ma_three_stage_screener import (
    ThreeStageParameters,
    scan_three_stage_pair,
)


STAGE_ORDER = {
    "entry_confirmed": 0,
    "wait_first_pullback": 1,
    "ready_4h_breakout_15m_coiled": 2,
    "watch_both_coiled": 3,
    "none": 4,
}


class RequestLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1 / requests_per_second
        self.lock = threading.Lock()
        self.last_started = 0.0

    def wait(self) -> None:
        with self.lock:
            delay = self.interval - (time.monotonic() - self.last_started)
            if delay > 0:
                time.sleep(delay)
            self.last_started = time.monotonic()


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
    output["stage_order"] = output["stage"].map(STAGE_ORDER).fillna(99)
    output["combined_score"] = (
        pd.to_numeric(output.get("four_hour_coil_score", 0), errors="coerce").fillna(0)
        + pd.to_numeric(
            output.get("fifteen_minute_coil_score", 0), errors="coerce"
        ).fillna(0)
    )
    output = output.sort_values(
        ["stage_order", "combined_score", "instrument"],
        ascending=[True, False, True],
        kind="stable",
    )
    return output.drop(columns=["stage_order", "combined_score"]).reset_index(drop=True)


def _fmt(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.{digits}f}" if np.isfinite(number) else "-"


def _time(value: object) -> str:
    return "-" if pd.isna(value) else pd.Timestamp(value).isoformat()


def _stage_table(results: pd.DataFrame, stage: str) -> str:
    frame = results.loc[(results["stage"] == stage) & (results["role"] == "trade")]
    if frame.empty:
        return "当前没有符合该阶段的合约。"
    lines = [
        "| 合约 | 方向 | 日线偏向 | 4H缠绕分 | 15m缠绕分 | 4H区间 | 15m区间 | 15m突破 | 首次回踩 | 止损 | 下一可执行时间 |",
        "|---|---|---|---:|---:|---|---|---|---|---:|---|",
    ]
    for _, row in frame.iterrows():
        direction = {"long": "多", "short": "空", "none": "待定"}.get(
            str(row["direction"]), str(row["direction"])
        )
        lines.append(
            f"| {row['instrument']} | {direction} | {row['daily_bias']} | "
            f"{_fmt(row['four_hour_coil_score'], 1)} | "
            f"{_fmt(row['fifteen_minute_coil_score'], 1)} | "
            f"{_fmt(row['four_hour_zone_low'])}–{_fmt(row['four_hour_zone_high'])} | "
            f"{_fmt(row['fifteen_minute_zone_low'])}–{_fmt(row['fifteen_minute_zone_high'])} | "
            f"{_time(row['fifteen_minute_breakout_at'])} | "
            f"{_time(row['first_pullback_at'])} | "
            f"{_fmt(row['initial_stop_price'], 8)} | "
            f"{_time(row['next_executable_at'])} |"
        )
    return "\n".join(lines)


def _near_stage_table(results: pd.DataFrame) -> str:
    reasons = {
        "fifteen_minute_coil_not_ready",
        "fifteen_minute_setup_expired",
    }
    frame = results.loc[
        (results["role"] == "trade") & results["reason"].isin(reasons)
    ].head(20)
    if frame.empty:
        return "当前没有接近下一阶段的合约。"
    lines = [
        "| 合约 | 方向 | 日线偏向 | 4H缠绕分 | 4H突破时间 | 当前缺少条件 |",
        "|---|---|---|---:|---|---|",
    ]
    labels = {
        "fifteen_minute_coil_not_ready": "等待15m形成新缠绕",
        "fifteen_minute_setup_expired": "15m等待已过期，仅作复盘",
    }
    for _, row in frame.iterrows():
        direction = {"long": "多", "short": "空"}.get(
            str(row["direction"]), "待定"
        )
        lines.append(
            f"| {row['instrument']} | {direction} | {row['daily_bias']} | "
            f"{_fmt(row['four_hour_coil_score'], 1)} | "
            f"{_time(row['four_hour_breakout_at'])} | "
            f"{labels.get(str(row['reason']), str(row['reason']))} |"
        )
    return "\n".join(lines)


def markdown_report(
    results: pd.DataFrame,
    retrieved_at: datetime,
    params: ThreeStageParameters,
    total_live: int,
    errors: dict[str, str],
) -> str:
    ordered = sort_results(results)
    trade = ordered.loc[ordered["role"] == "trade"]
    counts = trade["stage"].value_counts()
    return f"""# OKX 六均线三阶段筛选器

更新时间：{retrieved_at.astimezone().isoformat()}

## 规则摘要

- 全部信号只使用已收盘K线，核心指标只含 MA20/60/120、EMA20/60/120 与 ATR 标准化。
- 先找4H持续密集、震荡交织的六线 episode；4H突破后，必须重新形成15m持续缠绕。
- 15m同向突破后只认后续第一次回踩；第一次触碰失守即取消，不等第二次。
- 日线仅作方向偏向，不作为前两阶段硬门槛；BTC/ETH市场过滤只作用于加密类最终确认。
- `entry_confirmed` 仅在下一根15m开盘前有效。本表是人工筛选辅助，不是自动开仓指令，也不是盈利证明。

共检查 {total_live} 个 live USDT 永续；行情请求异常 {len(errors)} 个。当前交易合约状态：确认 {int(counts.get('entry_confirmed', 0))}、等待回踩 {int(counts.get('wait_first_pullback', 0))}、准备突破 {int(counts.get('ready_4h_breakout_15m_coiled', 0))}、双周期缠绕 {int(counts.get('watch_both_coiled', 0))}。

## 1. 回踩确认

{_stage_table(ordered, 'entry_confirmed')}

## 2. 等待第一次回踩

{_stage_table(ordered, 'wait_first_pullback')}

## 3. 4H已突破、15m重新缠绕

{_stage_table(ordered, 'ready_4h_breakout_15m_coiled')}

## 4. 4H与15m同时缠绕

{_stage_table(ordered, 'watch_both_coiled')}

## 接近下一阶段（不属于正式候选）

{_near_stage_table(ordered)}

## 参数口径

- 4H缠绕窗口 {params.coil_window_4h} 根；15m缠绕窗口 {params.coil_window_15m} 根。
- 4H突破有效 {params.zone_breakout_wait_4h} 根4H；突破后最多等待 {params.setup_wait_15m} 根15m重新缠绕。
- 15m突破后最多等待 {params.pullback_wait_15m} 根15m的第一次回踩。
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
        if not bool(instrument["eligible"]) or role != "trade":
            state = {
                "stage": "none",
                "direction": "none",
                "daily_bias": "neutral",
                "four_hour_coil_score": 0.0,
                "fifteen_minute_coil_score": 0.0,
                "reason": (
                    "reference_only"
                    if role == "reference"
                    else str(instrument["eligibility_reason"])
                ),
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
        rows.append(
            {
                "instrument": instrument_id,
                "role": role,
                "inst_category": instrument["instCategory"],
                "age_days": instrument["ageDays"],
                **state,
            }
        )
    return sort_results(pd.DataFrame(rows))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-age-days", type=int, default=30)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--requests-per-second", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/okx-three-stage-screener"),
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 12 or args.requests_per_second <= 0:
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
    eligible_trade = [
        row for row in live if row["role"] == "trade" and row["eligible"]
    ]
    trade_ids = [str(row["instId"]) for row in eligible_trade]
    reference_ids = [
        str(row["instId"])
        for row in live
        if row["role"] == "reference" and row["eligible"]
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
        live,
        daily,
        four_hour,
        fifteen,
        pd.Timestamp(retrieved_at),
        params,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output_dir / "latest.csv", index=False)
    report = markdown_report(results, retrieved_at, params, len(live), errors)
    (args.output_dir / "LATEST.md").write_text(report, encoding="utf-8")
    counts = results.loc[results["role"] == "trade", "stage"].value_counts()
    manifest = {
        "retrievedAt": retrieved_at.isoformat(),
        "parameters": asdict(params),
        "liveUsdtSwaps": len(live),
        "eligibleTradeContracts": len(eligible_trade),
        "stageCounts": {str(key): int(value) for key, value in counts.items()},
        "errors": errors,
        "claimBoundary": "current causal screening snapshot; not a profitability result",
    }
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    actionable = int(
        results["stage"].isin(
            {
                "entry_confirmed",
                "wait_first_pullback",
                "ready_4h_breakout_15m_coiled",
                "watch_both_coiled",
            }
        ).sum()
    )
    print(f"saved {actionable} active states to {args.output_dir / 'LATEST.md'}")


if __name__ == "__main__":
    main()
