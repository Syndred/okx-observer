#!/usr/bin/env python3
"""Download confirmed OKX 15m, mark-price, and funding history with resume support."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from user_data.strategy_lib.okx_candles import (
    confirmed_candles,
    funding_events,
    mark_candles,
    resample_confirmed,
)


API_ROOT = "https://www.okx.com"
USER_AGENT = "crypto-trading-system-research/2.0"


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def request_json(
    endpoint: str, params: dict[str, Any], retries: int = 6, timeout: int = 30
) -> dict[str, Any]:
    url = f"{API_ROOT}{endpoint}?{urlencode(params)}"
    for attempt in range(retries):
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if payload.get("code") != "0":
                raise RuntimeError(
                    f"OKX {endpoint} failed: {payload.get('code')} {payload.get('msg', '')}"
                )
            return payload
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise
        except URLError:
            if attempt == retries - 1:
                raise
        if attempt == retries - 1:
            raise RuntimeError(f"OKX request exhausted retries: {url}")
        time.sleep(min(2**attempt, 10))
    raise RuntimeError("unreachable")


def paginate_older(
    endpoint: str,
    instrument_id: str,
    start_ms: int,
    end_ms: int,
    *,
    bar: str | None,
    limit: int,
    timestamp_of: Callable[[Any], int],
    pause: float = 0.12,
) -> list[Any]:
    rows: list[Any] = []
    cursor: int | None = None
    seen_oldest: int | None = None
    while True:
        params: dict[str, Any] = {"instId": instrument_id, "limit": limit}
        if bar is not None:
            params["bar"] = bar
        if cursor is not None:
            params["after"] = cursor
        page = request_json(endpoint, params).get("data", [])
        if not page:
            break
        timestamps = [timestamp_of(row) for row in page]
        oldest = min(timestamps)
        if seen_oldest is not None and oldest >= seen_oldest:
            raise RuntimeError(f"Pagination stopped advancing for {instrument_id} {endpoint}")
        seen_oldest = oldest
        rows.extend(row for row, ts in zip(page, timestamps) if start_ms <= ts < end_ms)
        if oldest <= start_ms or len(page) < limit:
            break
        cursor = oldest
        time.sleep(pause)
    return rows


def merge_feather(path: Path, frame: pd.DataFrame) -> pd.DataFrame:
    if path.exists():
        previous = pd.read_feather(path)
        previous["date"] = pd.to_datetime(previous["date"], utc=True)
        frame = pd.concat([previous, frame], ignore_index=True)
    if not frame.empty:
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reset_index(drop=True).to_feather(path)
    return frame.reset_index(drop=True)


def download_symbol(
    instrument_id: str, start_ms: int, end_ms: int, output_dir: Path
) -> dict[str, Any]:
    trade_rows = paginate_older(
        "/api/v5/market/history-candles",
        instrument_id,
        start_ms,
        end_ms,
        bar="15m",
        limit=300,
        timestamp_of=lambda row: int(row[0]),
    )
    mark_rows = paginate_older(
        "/api/v5/market/history-mark-price-candles",
        instrument_id,
        start_ms,
        end_ms,
        bar="15m",
        limit=100,
        timestamp_of=lambda row: int(row[0]),
    )
    funding_rows = paginate_older(
        "/api/v5/public/funding-rate-history",
        instrument_id,
        start_ms,
        end_ms,
        bar=None,
        limit=100,
        timestamp_of=lambda row: int(row["fundingTime"]),
    )
    candles15 = confirmed_candles(trade_rows)
    marks15 = mark_candles(mark_rows)
    funding = funding_events(funding_rows)
    prefix = output_dir / instrument_id
    candles15 = merge_feather(prefix.with_name(prefix.name + "-15m.feather"), candles15)
    marks15 = merge_feather(prefix.with_name(prefix.name + "-mark-15m.feather"), marks15)
    funding = merge_feather(prefix.with_name(prefix.name + "-funding.feather"), funding)
    candles1h = resample_confirmed(candles15, "1h")
    candles4h = resample_confirmed(candles15, "4h")
    marks1h = resample_confirmed(marks15, "1h")
    marks4h = resample_confirmed(marks15, "4h")
    candles1h.to_feather(prefix.with_name(prefix.name + "-1h.feather"))
    candles4h.to_feather(prefix.with_name(prefix.name + "-4h.feather"))
    marks1h.to_feather(prefix.with_name(prefix.name + "-mark-1h.feather"))
    marks4h.to_feather(prefix.with_name(prefix.name + "-mark-4h.feather"))
    return {
        "instrument": instrument_id,
        "first_candle": candles15["date"].min().isoformat() if not candles15.empty else "",
        "last_candle": candles15["date"].max().isoformat() if not candles15.empty else "",
        "rows_15m": len(candles15),
        "rows_1h": len(candles1h),
        "rows_4h": len(candles4h),
        "mark_rows_15m": len(marks15),
        "funding_events": len(funding),
    }


def selected_symbols(snapshot_path: Path, explicit: list[str] | None) -> list[str]:
    if explicit:
        return explicit
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return [
        row["instId"]
        for row in snapshot["instruments"]
        if row["eligible"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--output-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument(
        "--report", type=Path, default=Path("reports/okx-v2/data-availability.csv")
    )
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    start_ms = utc_ms(args.start)
    end_ms = utc_ms(args.end)
    if args.smoke:
        start_ms = max(
            start_ms,
            int((datetime.fromtimestamp(end_ms / 1000, timezone.utc) - timedelta(days=7)).timestamp() * 1000),
        )
    symbols = selected_symbols(args.snapshot, args.symbols)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    summaries = []
    for index, instrument_id in enumerate(symbols, start=1):
        print(f"[{index}/{len(symbols)}] {instrument_id}", flush=True)
        summaries.append(download_symbol(instrument_id, start_ms, end_ms, args.output_dir))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(args.report, index=False)
    print(f"Saved {len(summaries)} symbols and report {args.report}")


if __name__ == "__main__":
    main()
