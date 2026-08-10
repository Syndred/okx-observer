#!/usr/bin/env python3
"""Download confirmed 1H candles for broad historical universe screening."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import pandas as pd
import requests

from scripts.download_okx_v2_data import (
    API_ROOT,
    USER_AGENT,
    merge_feather,
    paginate_older,
    utc_ms,
)
from user_data.strategy_lib.okx_candles import confirmed_candles


_thread_state = threading.local()
_rate_lock = threading.Lock()
_last_request_started = 0.0


def throttle_request_start(max_requests_per_second: float = 8.0) -> None:
    global _last_request_started
    with _rate_lock:
        delay = 1 / max_requests_per_second - (time.monotonic() - _last_request_started)
        if delay > 0:
            time.sleep(delay)
        _last_request_started = time.monotonic()


def pooled_request_json(
    endpoint: str, params: dict[str, object], retries: int = 6
) -> dict[str, object]:
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _thread_state.session = session
    for attempt in range(retries):
        try:
            throttle_request_start()
            response = session.get(f"{API_ROOT}{endpoint}", params=params, timeout=30)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"retryable HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise RuntimeError(
                    f"OKX {endpoint} failed: {payload.get('code')} {payload.get('msg', '')}"
                )
            return payload
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(min(2**attempt, 10))
    raise RuntimeError("unreachable")


def snapshot_symbols(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sorted(
        row["instId"]
        for row in payload["instruments"]
        if row.get("eligible") and row.get("role") in {"trade", "reference"}
    )


def download_hourly(
    instrument: str, start_ms: int, end_ms: int, output_dir: Path, pause: float
) -> dict[str, object]:
    path = output_dir / f"{instrument}-1h.feather"
    if path.exists():
        existing = pd.read_feather(path)
        if not existing.empty:
            existing["date"] = pd.to_datetime(existing["date"], utc=True)
            if int(existing["date"].max().timestamp() * 1000) >= end_ms - 2 * 3_600_000:
                return {
                    "instrument": instrument,
                    "first_candle": existing["date"].min().isoformat(),
                    "last_candle": existing["date"].max().isoformat(),
                    "rows_1h": len(existing),
                }
    rows = paginate_older(
        "/api/v5/market/history-candles",
        instrument,
        start_ms,
        end_ms,
        bar="1H",
        limit=300,
        timestamp_of=lambda row: int(row[0]),
        pause=pause,
        requester=pooled_request_json,
    )
    frame = confirmed_candles(rows)
    frame = merge_feather(path, frame)
    return {
        "instrument": instrument,
        "first_candle": frame["date"].min().isoformat() if not frame.empty else "",
        "last_candle": frame["date"].max().isoformat() if not frame.empty else "",
        "rows_1h": len(frame),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--start", default="2025-01-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--output-dir", type=Path, default=Path("user_data/data/okx_v2_hourly"))
    parser.add_argument("--report", type=Path, default=Path("reports/okx-v2/hourly-availability.csv"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pause", type=float, default=0.02)
    parser.add_argument("--max-symbols", type=int)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise SystemExit("workers must be between 1 and 8")
    symbols = snapshot_symbols(args.snapshot)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    summaries = []
    lock = threading.Lock()
    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download_hourly, symbol, start_ms, end_ms, args.output_dir, args.pause
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            summary = future.result()
            summaries.append(summary)
            with lock:
                completed += 1
                print(
                    f"[{completed}/{len(symbols)}] {summary['instrument']} rows={summary['rows_1h']}",
                    flush=True,
                )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).sort_values("instrument").to_csv(args.report, index=False)
    print(f"Saved {len(summaries)} hourly instruments and {args.report}")


if __name__ == "__main__":
    main()
