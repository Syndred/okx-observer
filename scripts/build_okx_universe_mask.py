#!/usr/bin/env python3
"""Build the lagged historical top-30 OKX V2 universe mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from user_data.strategy_lib.universe_ranker import (
    UniverseRules,
    build_universe_mask,
    eligible_trade_symbols,
)


def load_frames(data_dir: Path, snapshot_path: Path) -> dict[str, pd.DataFrame]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    instrument_to_symbol = eligible_trade_symbols(snapshot["instruments"])
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(data_dir.glob("*-1h.feather")):
        if "-mark-" in path.name:
            continue
        instrument = path.name.removesuffix("-1h.feather")
        symbol = instrument_to_symbol.get(instrument)
        if symbol is None:
            continue
        frame = pd.read_feather(path)
        frame["date"] = pd.to_datetime(frame["date"], utc=True)
        frames[symbol] = frame
    return frames


def summary(mask: pd.DataFrame) -> pd.DataFrame:
    if mask.empty:
        return pd.DataFrame(
            columns=["pair", "selected_hours", "first_selected", "last_selected", "median_rank"]
        )
    return (
        mask.groupby("pair", as_index=False)
        .agg(
            selected_hours=("date", "size"),
            first_selected=("date", "min"),
            last_selected=("date", "max"),
            median_rank=("rank", "median"),
        )
        .sort_values(["selected_hours", "pair"], ascending=[False, True])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("user_data/data/okx_v2/universe_top30.feather")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/okx-v2/universe-summary.csv")
    )
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--min-notional", type=float, default=1_000_000)
    args = parser.parse_args()
    frames = load_frames(args.data_dir, args.snapshot)
    if not frames:
        raise SystemExit("No eligible 1h OKX data files found")
    rules = UniverseRules(min_notional_24h=args.min_notional)
    mask = build_universe_mask(frames, limit=args.limit, rules=rules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mask.to_feather(args.output)
    report = summary(mask)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.report, index=False)
    print(
        f"Ranked {len(frames)} pairs across {mask['date'].nunique() if not mask.empty else 0} hours; "
        f"saved {len(mask)} selections"
    )


if __name__ == "__main__":
    main()
