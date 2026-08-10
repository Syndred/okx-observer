#!/usr/bin/env python3
"""Select every trade contract that entered the historical Top30 mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def selected_instruments(
    mask: pd.DataFrame, snapshot: dict[str, object], min_selected_hours: int
) -> list[str]:
    symbol_to_instrument = {
        row["symbol"]: row["instId"]
        for row in snapshot["instruments"]
        if row.get("eligible") and row.get("role") == "trade"
    }
    counts = mask.groupby("pair").size()
    selected = [
        symbol_to_instrument[pair]
        for pair, count in counts.items()
        if pair in symbol_to_instrument and int(count) >= min_selected_hours
    ]
    return sorted(selected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mask", type=Path, default=Path("user_data/data/okx_v2/universe_top30.feather"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--min-selected-hours", type=int, default=1)
    parser.add_argument(
        "--include",
        nargs="*",
        default=["BEAT-USDT-SWAP", "BLEND-USDT-SWAP", "XRP-USDT-SWAP"],
    )
    parser.add_argument("--output", type=Path, default=Path("user_data/okx_v2_research_cohort.json"))
    args = parser.parse_args()
    mask = pd.read_feather(args.mask)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    instruments = selected_instruments(mask, snapshot, args.min_selected_hours)
    eligible_trades = {
        row["instId"]
        for row in snapshot["instruments"]
        if row.get("eligible") and row.get("role") == "trade"
    }
    supplemental = sorted(set(args.include) & eligible_trades)
    instruments = sorted(set(instruments + supplemental))
    references = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    payload = {
        "selection_rule": f"historical_top30_selected_hours>={args.min_selected_hours}",
        "trade_instruments": instruments,
        "supplemental_research_instruments": supplemental,
        "reference_instruments": references,
        "download_instruments": sorted(set(instruments + references)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Selected {len(instruments)} trade instruments; saved {args.output}")


if __name__ == "__main__":
    main()
