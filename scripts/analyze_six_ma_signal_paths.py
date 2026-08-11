#!/usr/bin/env python3
"""Compare six-MA entry triggers without re-optimizing the 4H signal."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from scripts.run_okx_v2_research import (
    available_trade_pairs,
    build_events,
    clip_frame,
    load_frame,
    load_funding,
    load_snapshot,
    parse_utc_timestamp,
)
from user_data.strategy_lib.signal_path_analysis import analyze_signal_paths
from user_data.strategy_lib.six_ma_mtf_signal_engine import SixMaMtfParameters
from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)


TRIGGERS = ("compression_close", "nested_breakout", "pullback_rejection")


def diagnostic_parameters(trigger: str) -> SixMaMtfParameters:
    return SixMaMtfParameters(
        compression_4h_atr=2.0,
        breakout_4h_atr=0.05,
        compression_15m_atr=2.0,
        breakout_15m_atr=0.05,
        stop_buffer_atr=0.20,
        zone_max_age_4h=12,
        setup_wait_15m=48,
        entry_trigger=trigger,
        require_full_4h_trend=False,
        strict_market_consensus=False,
    )


def _path_summary(
    trigger: str, paths: pd.DataFrame, raw_event_count: int
) -> dict[str, object]:
    row: dict[str, object] = {
        "trigger": trigger,
        "raw_events": raw_event_count,
        "analyzable_events": len(paths),
        "complete_24h_events": 0,
    }
    if paths.empty or "complete_window" not in paths:
        return row
    complete = paths.loc[paths["complete_window"]].copy()
    row["complete_24h_events"] = len(complete)
    if complete.empty:
        return row
    for hours in (1, 3, 6, 12, 24):
        returns = complete[f"return_{hours}h_r"].dropna()
        row[f"positive_{hours}h_rate"] = float((returns > 0).mean())
        row[f"mean_return_{hours}h_r"] = float(returns.mean())
        row[f"median_return_{hours}h_r"] = float(returns.median())
        row[f"median_mfe_{hours}h_r"] = float(
            complete[f"mfe_{hours}h_r"].median()
        )
        row[f"median_mae_{hours}h_r"] = float(
            complete[f"mae_{hours}h_r"].median()
        )
    for target_r in (1, 2, 3, 5):
        row[f"target_{target_r}r_before_stop_rate"] = float(
            complete[f"target_{target_r}r_before_stop"].mean()
        )
    row["stop_rate_24h"] = float(complete["stop_hit"].mean())
    row["stopped_then_3r_rate"] = float(complete["stopped_then_3r"].mean())
    row["stopped_then_5r_rate"] = float(complete["stopped_then_5r"].mean())
    return row


def _group_summary(paths: pd.DataFrame, column: str) -> pd.DataFrame:
    if paths.empty or "complete_window" not in paths:
        return pd.DataFrame(columns=[column, "events"])
    complete = paths.loc[paths["complete_window"]].copy()
    rows = []
    for name, group in complete.groupby(column, sort=True):
        rows.append(
            {
                column: name,
                "events": len(group),
                "positive_6h_rate": float((group["return_6h_r"] > 0).mean()),
                "mean_return_6h_r": float(group["return_6h_r"].mean()),
                "positive_24h_rate": float((group["return_24h_r"] > 0).mean()),
                "mean_return_24h_r": float(group["return_24h_r"].mean()),
                "median_mfe_24h_r": float(group["mfe_24h_r"].median()),
                "median_mae_24h_r": float(group["mae_24h_r"].median()),
                "target_2r_before_stop_rate": float(
                    group["target_2r_before_stop"].mean()
                ),
                "target_3r_before_stop_rate": float(
                    group["target_3r_before_stop"].mean()
                ),
                "stop_rate_24h": float(group["stop_hit"].mean()),
                "stopped_then_3r_rate": float(
                    group["stopped_then_3r"].mean()
                ),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("user_data/data/okx_v2")
    )
    parser.add_argument(
        "--snapshot", type=Path, default=Path("config/okx_v2_universe.json")
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("user_data/data/okx_v2/universe_top30.feather"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("user_data/backtest_results/okx-sixma-paths-recent6m"),
    )
    parser.add_argument("--start", default="2026-02-10")
    parser.add_argument("--end", default="2026-08-11")
    parser.add_argument("--warmup-days", type=int, default=40)
    parser.add_argument("--signal-workers", type=int, default=4)
    parser.add_argument("--pairs", nargs="*")
    args = parser.parse_args()
    if args.signal_workers < 1 or args.signal_workers > 8:
        raise SystemExit("signal-workers must be between 1 and 8")
    start = parse_utc_timestamp(args.start)
    end = parse_utc_timestamp(args.end)
    if start is None or end is None or start >= end:
        raise SystemExit("valid --start and --end are required")
    load_start = start - pd.Timedelta(days=args.warmup_days)

    instruments, categories = load_snapshot(args.snapshot)
    pairs = available_trade_pairs(args.data_dir, instruments, args.pairs)
    btc4h = clip_frame(
        load_frame(args.data_dir, instruments["BTC/USDT:USDT"], "4h"),
        load_start,
        end,
    )
    eth4h = clip_frame(
        load_frame(args.data_dir, instruments["ETH/USDT:USDT"], "4h"),
        load_start,
        end,
    )
    frames = {
        pair: {
            timeframe: clip_frame(
                load_frame(args.data_dir, instruments[pair], timeframe),
                load_start,
                end,
            )
            for timeframe in ("15m", "4h")
        }
        for pair in pairs
    }
    base_frames = {pair: frame["15m"] for pair, frame in frames.items()}
    funding_frames = {
        pair: clip_frame(
            load_funding(args.data_dir, instruments[pair]), load_start, end
        )
        for pair in pairs
    }
    universe = pd.read_feather(args.universe)
    universe["date"] = pd.to_datetime(universe["date"], utc=True)
    universe = clip_frame(universe, load_start, end)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    portfolio_rows = []
    for trigger in TRIGGERS:
        params = diagnostic_parameters(trigger)

        def scan_pair(pair: str) -> list[EntryEvent]:
            return build_events(
                pair,
                categories[pair],
                frames,
                btc4h,
                eth4h,
                universe,
                params,
                "six_ma_mtf",
            )

        with ThreadPoolExecutor(max_workers=args.signal_workers) as pool:
            events = [
                event
                for pair_events in pool.map(scan_pair, pairs)
                for event in pair_events
                if start <= event.date < end
            ]
        paths = analyze_signal_paths(base_frames, events)
        paths.to_csv(args.output_dir / f"{trigger}-paths.csv", index=False)
        side_summary = _group_summary(paths, "side")
        side_summary.insert(0, "trigger", trigger)
        side_summary.to_csv(
            args.output_dir / f"{trigger}-side-summary.csv", index=False
        )
        pair_summary = _group_summary(paths, "pair")
        pair_summary.insert(0, "trigger", trigger)
        pair_summary.to_csv(
            args.output_dir / f"{trigger}-pair-summary.csv", index=False
        )
        summary_rows.append(_path_summary(trigger, paths, len(events)))

        normal = simulate_portfolio(
            base_frames,
            events,
            BacktestOptions(),
            start=start,
            end=end,
            funding_frames=funding_frames,
        )
        stress = simulate_portfolio(
            base_frames,
            events,
            BacktestOptions(
                fee_rate=0.001,
                slippage_rate=0.001,
                missing_funding_rate_per_8h=0.0002,
            ),
            start=start,
            end=end,
            funding_frames=funding_frames,
        )
        metrics = summarize_backtest(normal)
        portfolio_rows.append(
            {
                "trigger": trigger,
                **metrics,
                "stress_pf": summarize_backtest(stress)["pf"],
            }
        )
        print(
            f"{trigger}: events={len(events)} complete_paths="
            f"{int(paths['complete_window'].sum()) if 'complete_window' in paths else 0} "
            f"portfolio_trades={metrics['trades']} pf={metrics['pf']:.3f}",
            flush=True,
        )

    pd.DataFrame(summary_rows).to_csv(
        args.output_dir / "path-summary.csv", index=False
    )
    pd.DataFrame(portfolio_rows).to_csv(
        args.output_dir / "portfolio-comparison.csv", index=False
    )
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "purpose": "six_ma_direction_vs_execution_diagnostic",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "pairs": pairs,
                "triggers": list(TRIGGERS),
                "parameters": {
                    trigger: asdict(diagnostic_parameters(trigger))
                    for trigger in TRIGGERS
                },
                "note": "Same 4H parameters for all triggers; diagnostic, not fresh holdout.",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
