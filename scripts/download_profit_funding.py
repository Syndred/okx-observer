#!/usr/bin/env python3
"""Collect funding only; do not inspect holdout prices or performance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.download_okx_v2_data import funding_events, paginate_older, request_json, utc_ms


GROUPS = {
    "development": ("okx_scalp", "2026-07-28", "BTC ETH SOL XRP DOGE ADA AVAX LINK"),
    "holdout": ("okx_profit_holdout", "2026-08-08", "SUI NEAR UNI AAVE LTC BNB DOT ATOM"),
}


def download_group(group: str, base_dir: Path, pause: float = .6) -> dict:
    directory, start, symbols = GROUPS[group]
    output = base_dir / directory
    output.mkdir(parents=True, exist_ok=True)
    end = "2026-09-26"
    manifest = {
        "source": "OKX public funding-rate-history",
        "group": group,
        "requested_start_inclusive": start + "T00:00:00+00:00",
        "requested_end_exclusive": end + "T00:00:00+00:00",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbols": [], "errors": [],
        "accounting": "Actual signed settlements; missing UTC 00/08/16 settlements are conservatively imputed by simulation.",
    }

    def gentle_request(endpoint, params):
        time.sleep(pause)
        return request_json(endpoint, params)

    for symbol in symbols.split():
        instrument = symbol + "-USDT-SWAP"
        try:
            rows = paginate_older(
                "/api/v5/public/funding-rate-history", instrument,
                utc_ms(start), utc_ms(end), bar=None, limit=100,
                timestamp_of=lambda row: int(row["fundingTime"]),
                pause=0, requester=gentle_request,
            )
            frame = funding_events(rows)
            path = output / (instrument + "-funding.feather")
            frame.to_feather(path)
            manifest["symbols"].append({
                "instrument": instrument, "rows": len(frame), "errors": [],
                "first_settlement": None if frame.empty else frame.date.min().isoformat(),
                "last_settlement": None if frame.empty else frame.date.max().isoformat(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file": str(path),
            })
            print(json.dumps({"group": group, "instrument": instrument, "funding_rows": len(frame)}), flush=True)
        except Exception as error:
            manifest["errors"].append({"instrument": instrument, "error": str(error)})
        (output / "funding-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=["all", *GROUPS], default="all")
    parser.add_argument("--base-dir", type=Path, default=Path("user_data/data"))
    parser.add_argument("--pause", type=float, default=.6)
    args = parser.parse_args()
    if args.pause < .6:
        parser.error("pause must be >= .6 seconds")
    groups = GROUPS if args.group == "all" else [args.group]
    results = [download_group(group, args.base_dir, args.pause) for group in groups]
    if any(item["errors"] for item in results):
        raise SystemExit("Funding download incomplete; inspect funding-manifest.json errors")


if __name__ == "__main__":
    main()
