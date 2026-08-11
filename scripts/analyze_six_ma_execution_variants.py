#!/usr/bin/env python3
"""Test stop width, direction, and exit variants for frozen six-MA signals."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import pandas as pd

from scripts.run_okx_v2_research import clip_frame, load_frame, load_funding, load_snapshot
from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)


def scaled_events(
    paths: pd.DataFrame, stop_multiplier: float, direction: str
) -> list[EntryEvent]:
    if not math.isfinite(stop_multiplier) or stop_multiplier <= 0:
        raise ValueError("stop_multiplier must be positive")
    if direction not in {"both", "long", "short"}:
        raise ValueError("direction must be both, long, or short")
    source = paths if direction == "both" else paths.loc[paths["side"] == direction]
    events = []
    for row in source.itertuples(index=False):
        if row.side not in {"long", "short"}:
            raise ValueError("path side must be long or short")
        entry_price = float(row.entry_price)
        risk_price = float(row.risk_price) * stop_multiplier
        stop_price = (
            entry_price - risk_price if row.side == "long" else entry_price + risk_price
        )
        events.append(
            EntryEvent(
                date=pd.Timestamp(row.entry_date),
                pair=str(row.pair),
                side=str(row.side),
                stop_price=stop_price,
                rank=int(row.rank),
                category=str(row.category),
            )
        )
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--paths",
        type=Path,
        default=Path(
            "user_data/backtest_results/okx-sixma-paths-recent6m/"
            "pullback_rejection-paths.csv"
        ),
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("user_data/data/okx_v2")
    )
    parser.add_argument(
        "--snapshot", type=Path, default=Path("config/okx_v2_universe.json")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "user_data/backtest_results/okx-sixma-execution-recent6m-final"
        ),
    )
    parser.add_argument("--start", default="2026-02-10")
    parser.add_argument("--end", default="2026-08-11")
    parser.add_argument("--stress-top", type=int, default=20)
    args = parser.parse_args()
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    if start >= end:
        raise SystemExit("--start must be earlier than --end")

    paths = pd.read_csv(args.paths)
    paths["entry_date"] = pd.to_datetime(paths["entry_date"], utc=True)
    paths = paths.loc[(paths["entry_date"] >= start) & (paths["entry_date"] < end)]
    if "complete_window" not in paths:
        raise SystemExit("paths must include complete_window")
    complete_window = paths["complete_window"].astype(str).str.lower().map(
        {"true": True, "false": False}
    )
    if complete_window.isna().any():
        raise SystemExit("complete_window must contain only true/false")
    paths = paths.loc[complete_window].copy()
    instruments, _ = load_snapshot(args.snapshot)
    pairs = sorted(paths["pair"].unique())
    base_frames = {
        pair: clip_frame(
            load_frame(args.data_dir, instruments[pair], "15m"), start, end
        )
        for pair in pairs
    }
    funding_frames = {
        pair: clip_frame(
            load_funding(args.data_dir, instruments[pair]), start, end
        )
        for pair in pairs
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    event_cache: dict[tuple[str, float], list[EntryEvent]] = {}
    for direction in ("both", "long", "short"):
        for stop_multiplier in (1.0, 1.5, 2.0, 3.0):
            events = scaled_events(paths, stop_multiplier, direction)
            event_cache[(direction, stop_multiplier)] = events
            for exit_mode in ("hybrid", "fixed3", "fixed5"):
                for time_mode in ("default", "24h", "none"):
                    options = BacktestOptions(
                        exit_mode=exit_mode,
                        time_mode=time_mode,
                    )
                    result = simulate_portfolio(
                        base_frames,
                        events,
                        options,
                        start=start,
                        end=end,
                        funding_frames=funding_frames,
                    )
                    rows.append(
                        {
                            "direction": direction,
                            "stop_multiplier": stop_multiplier,
                            "exit_mode": exit_mode,
                            "time_mode": time_mode,
                            **summarize_backtest(result),
                            "stress_pf": pd.NA,
                        }
                    )
    matrix = pd.DataFrame(rows)
    eligible = matrix.loc[matrix["trades"] >= 20].sort_values(
        ["pf", "drawdown", "trades"], ascending=[False, True, False]
    )
    for index in eligible.head(args.stress_top).index:
        row = matrix.loc[index]
        events = event_cache[(str(row["direction"]), float(row["stop_multiplier"]))]
        stress = simulate_portfolio(
            base_frames,
            events,
            BacktestOptions(
                exit_mode=str(row["exit_mode"]),
                time_mode=str(row["time_mode"]),
                fee_rate=0.001,
                slippage_rate=0.001,
                missing_funding_rate_per_8h=0.0002,
            ),
            start=start,
            end=end,
            funding_frames=funding_frames,
        )
        matrix.at[index, "stress_pf"] = summarize_backtest(stress)["pf"]

    matrix = matrix.sort_values(
        ["pf", "drawdown", "trades"], ascending=[False, True, False]
    )
    matrix.to_csv(args.output_dir / "execution-matrix.csv", index=False)
    forward_events = event_cache[("short", 1.0)]
    forward_options = BacktestOptions(exit_mode="hybrid", time_mode="24h")
    forward_result = simulate_portfolio(
        base_frames,
        forward_events,
        forward_options,
        start=start,
        end=end,
        funding_frames=funding_frames,
    )
    forward_stress = simulate_portfolio(
        base_frames,
        forward_events,
        BacktestOptions(
            exit_mode="hybrid",
            time_mode="24h",
            fee_rate=0.001,
            slippage_rate=0.001,
            missing_funding_rate_per_8h=0.0002,
        ),
        start=start,
        end=end,
        funding_frames=funding_frames,
    )
    forward_metrics = summarize_backtest(forward_result)
    forward_stress_metrics = summarize_backtest(forward_stress)
    pd.DataFrame([asdict(trade) for trade in forward_result.trades]).to_csv(
        args.output_dir / "forward-candidate-trades.csv", index=False
    )
    forward_result.equity_curve.to_csv(
        args.output_dir / "forward-candidate-equity.csv", index=False
    )
    monthly_rows = []
    month_starts = pd.date_range(start.floor("D"), end, freq="MS", tz="UTC")
    if month_starts.empty or month_starts[0] > start:
        month_starts = month_starts.insert(0, start)
    for month_start in month_starts:
        window_start = max(start, pd.Timestamp(month_start))
        window_end = min(end, pd.Timestamp(month_start) + pd.offsets.MonthBegin(1))
        if window_start >= window_end:
            continue
        monthly = simulate_portfolio(
            base_frames,
            forward_events,
            forward_options,
            start=window_start,
            end=window_end,
            funding_frames=funding_frames,
        )
        monthly_stress = simulate_portfolio(
            base_frames,
            forward_events,
            BacktestOptions(
                exit_mode="hybrid",
                time_mode="24h",
                fee_rate=0.001,
                slippage_rate=0.001,
                missing_funding_rate_per_8h=0.0002,
            ),
            start=window_start,
            end=window_end,
            funding_frames=funding_frames,
        )
        monthly_rows.append(
            {
                "start": window_start,
                "end": window_end,
                **summarize_backtest(monthly),
                "stress_pf": summarize_backtest(monthly_stress)["pf"],
            }
        )
    pd.DataFrame(monthly_rows).to_csv(
        args.output_dir / "forward-candidate-monthly.csv", index=False
    )
    (args.output_dir / "forward-candidate.json").write_text(
        json.dumps(
            {
                "status": "diagnostic_only_requires_fresh_forward_data",
                "direction": "short",
                "stop_multiplier": 1.0,
                "exit_mode": "hybrid",
                "time_mode": "24h",
                "metrics": forward_metrics,
                "stress_metrics": forward_stress_metrics,
                "warning": (
                    "Chosen after inspecting the same six-month period; not an "
                    "independent holdout and not authorized for live trading."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "purpose": "post_holdout_execution_diagnostic",
                "warning": (
                    "The period has already been inspected. Results can nominate a "
                    "forward-test hypothesis but cannot validate live feasibility."
                ),
                "paths": str(args.paths),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "pairs": pairs,
                "stop_multipliers": [1.0, 1.5, 2.0, 3.0],
                "directions": ["both", "long", "short"],
                "options": asdict(BacktestOptions()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    best = matrix.iloc[0]
    print(
        f"best diagnostic: direction={best['direction']} stop={best['stop_multiplier']} "
        f"exit={best['exit_mode']}/{best['time_mode']} trades={best['trades']} "
        f"pf={best['pf']:.3f} stress_pf={best['stress_pf']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
