#!/usr/bin/env python3
"""Download 15m trade candles and funding for the historical Top30 cohort."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from scripts.download_okx_v2_data import merge_feather, paginate_older, utc_ms
from scripts.download_okx_v2_hourly import pooled_request_json
from user_data.strategy_lib.okx_candles import confirmed_candles, funding_events, resample_confirmed


def download_instrument(
    instrument: str,
    start_ms: int,
    end_ms: int,
    output_dir: Path,
    pause: float,
) -> dict[str, object]:
    candle_path = output_dir / f"{instrument}-15m.feather"
    existing_complete = False
    if candle_path.exists():
        existing = pd.read_feather(candle_path)
        if not existing.empty:
            existing["date"] = pd.to_datetime(existing["date"], utc=True)
            existing_complete = (
                int(existing["date"].max().timestamp() * 1000) >= end_ms - 30 * 60_000
                and int(existing["date"].min().timestamp() * 1000) <= start_ms + 15 * 60_000
            )
    if existing_complete:
        candles15 = existing
    else:
        rows = paginate_older(
            "/api/v5/market/history-candles",
            instrument,
            start_ms,
            end_ms,
            bar="15m",
            limit=300,
            timestamp_of=lambda row: int(row[0]),
            pause=pause,
            requester=pooled_request_json,
        )
        candles15 = merge_feather(candle_path, confirmed_candles(rows))
    funding_rows = paginate_older(
        "/api/v5/public/funding-rate-history",
        instrument,
        start_ms,
        end_ms,
        bar=None,
        limit=100,
        timestamp_of=lambda row: int(row["fundingTime"]),
        pause=pause,
        requester=pooled_request_json,
    )
    funding = merge_feather(
        output_dir / f"{instrument}-funding.feather", funding_events(funding_rows)
    )
    candles1h = resample_confirmed(candles15, "1h")
    candles4h = resample_confirmed(candles15, "4h")
    candles1h.to_feather(output_dir / f"{instrument}-1h.feather")
    candles4h.to_feather(output_dir / f"{instrument}-4h.feather")
    return {
        "instrument": instrument,
        "first_candle": candles15["date"].min().isoformat() if not candles15.empty else "",
        "last_candle": candles15["date"].max().isoformat() if not candles15.empty else "",
        "rows_15m": len(candles15),
        "rows_1h": len(candles1h),
        "rows_4h": len(candles4h),
        "funding_events": len(funding),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, default=Path("config/okx_v2_research_cohort.json"))
    parser.add_argument("--start", default="2025-01-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--output-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--report", type=Path, default=Path("reports/okx-v2/data-availability.csv"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pause", type=float, default=0.02)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise SystemExit("workers must be between 1 and 8")
    payload = json.loads(args.cohort.read_text(encoding="utf-8"))
    instruments = payload["download_instruments"]
    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    summaries = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download_instrument,
                instrument,
                start_ms,
                end_ms,
                args.output_dir,
                args.pause,
            ): instrument
            for instrument in instruments
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            summary = future.result()
            summaries.append(summary)
            print(
                f"[{completed}/{len(instruments)}] {summary['instrument']} rows15m={summary['rows_15m']}",
                flush=True,
            )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).sort_values("instrument").to_csv(args.report, index=False)
    print(f"Saved {len(summaries)} cohort instruments and {args.report}")


if __name__ == "__main__":
    main()
