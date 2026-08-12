#!/usr/bin/env python3
"""Scan live OKX USDT swaps for daily/4H trend plus 15m MA compression."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import pandas as pd

from scripts.download_okx_v2_data import request_json
from scripts.snapshot_okx_instruments import fetch_instruments, normalize_instruments
from user_data.strategy_lib.okx_candles import confirmed_candles
from user_data.strategy_lib.okx_trend_compression_screener import (
    ScreenerParameters,
    screen_instruments,
    trend_snapshot,
)


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
            return instrument_id, fetch_candles(instrument_id, bar, limit, limiter), None
        except Exception as error:  # Preserve partial scanner results.
            return instrument_id, pd.DataFrame(), f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for instrument_id, frame, error in pool.map(fetch, instruments):
            frames[instrument_id] = frame
            if error:
                errors[instrument_id] = error
    return frames, errors


def markdown_report(
    results: pd.DataFrame,
    retrieved_at: datetime,
    params: ScreenerParameters,
    total_live: int,
    errors: dict[str, str],
) -> str:
    candidates = results.loc[results["candidate"]].copy()
    priority = candidates.loc[
        candidates["daily_strict_six"] & candidates["4h_strict_six"]
    ]
    ordinary = candidates.loc[
        ~(candidates["daily_strict_six"] & candidates["4h_strict_six"])
    ]
    aligned = results.loc[
        (results["status"] == "aligned_waiting_for_compression")
    ].head(20)

    def table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "当前没有符合条件的合约。"
        lines = [
            "| 合约 | 方向 | 15m密集度/ATR | 日线六线严格排列 | 4H六线严格排列 | 最新价 |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for _, row in frame.iterrows():
            lines.append(
                f"| {row['instrument']} | {'多' if row['direction'] == 'long' else '空'} | "
                f"{float(row['compression_15m_atr']):.3f} | "
                f"{'是' if row['daily_strict_six'] else '否'} | "
                f"{'是' if row['4h_strict_six'] else '否'} | "
                f"{float(row['last_price']):.8g} |"
            )
        return "\n".join(lines)

    long_count = int((candidates["direction"] == "long").sum())
    short_count = int((candidates["direction"] == "short").sum())
    return f"""# OKX 日线 + 4H 趋势 / 15m 六均线密集筛选

更新时间：{retrieved_at.astimezone().isoformat()}

## 本轮规则

- 范围：OKX 当前全部 USDT 永续；BTC/ETH 只作参考，不列为交易候选
- 上市时间：至少 {params.min_age_days} 天
- 趋势：日线和 4H 同时满足 EMA20/EMA60 同向、价格位于 EMA20 同侧、EMA60 斜率同向
- 入场观察：15m 的 MA20/60/120 + EMA20/60/120 六线跨度 ≤ {params.compression_15m_atr:.2f} ATR
- 只使用已经收盘的 K 线；本表是筛选结果，不是自动开仓指令

共检查 {total_live} 个 live USDT 永续，正式候选 {len(candidates)} 个：多 {long_count}、空 {short_count}。行情请求异常 {len(errors)} 个。

## A 级：日线、4H 六线也完整顺排

{table(priority)}

## B 级：EMA20/60 趋势同向，15m 已密集

{table(ordinary)}

## 日线与 4H 已同向、等待 15m 更密集（最近 20 个）

{table(aligned)}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compression-atr", type=float, default=2.0)
    parser.add_argument("--min-age-days", type=int, default=30)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--requests-per-second", type=float, default=10.0)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/okx-screener")
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 12 or args.requests_per_second <= 0:
        raise SystemExit("invalid workers or requests-per-second")
    params = ScreenerParameters(args.compression_atr, args.min_age_days)
    retrieved_at = datetime.now(timezone.utc)
    normalized = normalize_instruments(
        fetch_instruments(),
        now_ms=int(retrieved_at.timestamp() * 1000),
        min_age_days=args.min_age_days,
    )
    live = [row for row in normalized if row["state"] == "live"]
    trade_contracts = [row for row in live if row["role"] == "trade"]
    eligible = [
        row
        for row in trade_contracts
        if row["eligible"]
    ]
    instrument_ids = [str(row["instId"]) for row in eligible]
    limiter = RequestLimiter(args.requests_per_second)

    print(f"[1/3] scanning daily trend for {len(instrument_ids)} contracts", flush=True)
    daily, daily_errors = fetch_stage(
        instrument_ids, "1Dutc", 150, args.workers, limiter
    )
    daily_trending = [
        instrument_id
        for instrument_id in instrument_ids
        if trend_snapshot(daily[instrument_id])["direction"] in {"long", "short"}
    ]

    print(f"[2/3] scanning 4H trend for {len(daily_trending)} contracts", flush=True)
    four_hour, four_hour_errors = fetch_stage(
        daily_trending, "4H", 150, args.workers, limiter
    )
    aligned = [
        instrument_id
        for instrument_id in daily_trending
        if trend_snapshot(daily[instrument_id])["direction"]
        == trend_snapshot(four_hour[instrument_id])["direction"]
        and trend_snapshot(four_hour[instrument_id])["direction"] in {"long", "short"}
    ]

    print(f"[3/3] measuring 15m compression for {len(aligned)} contracts", flush=True)
    fifteen, fifteen_errors = fetch_stage(
        aligned, "15m", 150, args.workers, limiter
    )
    errors = {**daily_errors, **four_hour_errors, **fifteen_errors}
    results = screen_instruments(trade_contracts, daily, four_hour, fifteen, params)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output_dir / "latest.csv", index=False)
    report = markdown_report(results, retrieved_at, params, len(live), errors)
    (args.output_dir / "LATEST.md").write_text(report, encoding="utf-8")
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "retrievedAt": retrieved_at.isoformat(),
                "parameters": {
                    "compression_15m_atr": params.compression_15m_atr,
                    "min_age_days": params.min_age_days,
                    "trend": "daily_and_4h_ema20_above_or_below_ema60_with_slope",
                    "compression": "15m_six_average_spread_divided_by_atr14",
                },
                "liveUsdtSwaps": len(live),
                "eligibleTradeContracts": len(eligible),
                "dailyTrending": len(daily_trending),
                "dailyAnd4hAligned": len(aligned),
                "candidateCount": int(results["candidate"].sum()),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"saved {int(results['candidate'].sum())} candidates to "
        f"{args.output_dir / 'LATEST.md'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
