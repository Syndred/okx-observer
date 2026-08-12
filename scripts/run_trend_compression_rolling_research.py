#!/usr/bin/env python3
"""Research daily/4H trend, 15m compression, and rolling margin allocation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from scripts.run_okx_v2_research import (
    available_trade_pairs,
    clip_frame,
    load_frame,
    load_funding,
    load_snapshot,
    parse_utc_timestamp,
)
from user_data.strategy_lib.okx_candles import resample_confirmed
from user_data.strategy_lib.trend_compression_history import (
    TrendCompressionParameters,
    scan_trend_compression_setups,
)
from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)


def build_pair_events(
    pair: str,
    category: str,
    candles15m: pd.DataFrame,
    candles4h: pd.DataFrame,
    candles1d: pd.DataFrame,
    params: TrendCompressionParameters,
) -> list[tuple[pd.Timestamp, str, str, float, str, float]]:
    signals = scan_trend_compression_setups(
        pair=pair,
        candles15m=candles15m,
        candles4h=candles4h,
        candles1d=candles1d,
        universe_mask=None,
        params=params,
    )
    volume = (
        candles15m.sort_values("date")
        .drop_duplicates("date", keep="last")
        .set_index("date")["quote_volume"]
        .rolling(96, min_periods=96)
        .sum()
        .shift(1)
    )
    events: list[tuple[pd.Timestamp, str, str, float, str, float]] = []
    for index, row in signals.iterrows():
        side = "long" if int(row["enter_long"]) else "short" if int(row["enter_short"]) else None
        if side is None or index + 1 >= len(signals):
            continue
        next_row = signals.iloc[index + 1]
        entry_date = pd.Timestamp(next_row["date"])
        entry_price = float(next_row["open"])
        stop_price = float(row["initial_stop_price"])
        correctly_sided = stop_price < entry_price if side == "long" else stop_price > entry_price
        if not correctly_sided or not pd.notna(stop_price):
            continue
        liquidity = float(volume.get(entry_date, 0.0))
        events.append((entry_date, pair, side, stop_price, category, liquidity))
    return events


def rank_events(
    raw_events: list[tuple[pd.Timestamp, str, str, float, str, float]],
) -> list[EntryEvent]:
    grouped: dict[pd.Timestamp, list[tuple[pd.Timestamp, str, str, float, str, float]]] = {}
    for event in raw_events:
        grouped.setdefault(event[0], []).append(event)
    ranked: list[EntryEvent] = []
    for date, group in grouped.items():
        ordered = sorted(group, key=lambda item: (-item[5], item[1], item[2]))
        for rank, (_, pair, side, stop, category, _) in enumerate(ordered, start=1):
            ranked.append(EntryEvent(date, pair, side, stop, rank, category))
    return sorted(ranked, key=lambda item: (item.date, item.rank, item.pair))


def rolling_options(
    *,
    collateral_fraction: float,
    leverage: float,
    account_profit_target: float,
    time_mode: str,
    stress: bool = False,
    compounding_cap_multiple: float | None = None,
) -> BacktestOptions:
    return BacktestOptions(
        leverage=leverage,
        exit_mode="margin",
        time_mode=time_mode,
        fee_rate=0.001 if stress else 0.0005,
        slippage_rate=0.001 if stress else 0.0005,
        normal_trade_risk=0.10,
        reduced_trade_risk=0.05,
        max_portfolio_risk=0.20,
        collateral_fraction=collateral_fraction,
        margin_take_profit=account_profit_target / collateral_fraction,
        missing_funding_rate_per_8h=0.0002 if stress else 0.0001,
        compounding_cap_multiple=compounding_cap_multiple,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--start", default="2026-02-10")
    parser.add_argument("--selection-end", default="2026-06-11")
    parser.add_argument("--end", default="2026-08-11")
    parser.add_argument("--warmup-days", type=int, default=180)
    parser.add_argument("--signal-workers", type=int, default=4)
    parser.add_argument("--pairs", nargs="*")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("user_data/backtest_results/okx-trend-compression-rolling"),
    )
    args = parser.parse_args()
    start = parse_utc_timestamp(args.start)
    selection_end = parse_utc_timestamp(args.selection_end)
    end = parse_utc_timestamp(args.end)
    if None in {start, selection_end, end} or not start < selection_end < end:
        raise SystemExit("require start < selection-end < end")
    if not 1 <= args.signal_workers <= 8:
        raise SystemExit("signal-workers must be between 1 and 8")
    load_start = start - pd.Timedelta(days=args.warmup_days)
    instruments, categories = load_snapshot(args.snapshot)
    pairs = available_trade_pairs(args.data_dir, instruments, args.pairs)
    frames15m = {
        pair: clip_frame(load_frame(args.data_dir, instruments[pair], "15m"), load_start, end)
        for pair in pairs
    }
    frames4h = {
        pair: clip_frame(load_frame(args.data_dir, instruments[pair], "4h"), load_start, end)
        for pair in pairs
    }
    frames1d = {pair: resample_confirmed(frame, "1d") for pair, frame in frames15m.items()}
    funding = {
        pair: clip_frame(load_funding(args.data_dir, instruments[pair]), load_start, end)
        for pair in pairs
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)

    event_sets: dict[str, list[EntryEvent]] = {}
    for trend_mode in ("basic", "strict_six"):
        params = TrendCompressionParameters(trend_mode=trend_mode)

        def scan(pair: str) -> list[tuple[pd.Timestamp, str, str, float, str, float]]:
            return build_pair_events(
                pair,
                categories[pair],
                frames15m[pair],
                frames4h[pair],
                frames1d[pair],
                params,
            )

        with ThreadPoolExecutor(max_workers=args.signal_workers) as pool:
            raw = [event for pair_events in pool.map(scan, pairs) for event in pair_events]
        event_sets[trend_mode] = rank_events(
            [event for event in raw if start <= event[0] < end]
        )
        print(f"{trend_mode}: {len(event_sets[trend_mode])} events", flush=True)

    rows: list[dict[str, object]] = []
    for trend_mode, events in event_sets.items():
        for direction in ("both", "long", "short"):
            selected_events = events if direction == "both" else [event for event in events if event.side == direction]
            for collateral_fraction in (0.30, 0.40):
                for leverage in (3.0, 5.0):
                    for account_profit_target in (0.20, 0.30):
                        for time_mode in ("24h", "72h"):
                            options = rolling_options(
                                collateral_fraction=collateral_fraction,
                                leverage=leverage,
                                account_profit_target=account_profit_target,
                                time_mode=time_mode,
                            )
                            normal = simulate_portfolio(
                                frames15m,
                                selected_events,
                                options,
                                start=start,
                                end=selection_end,
                                funding_frames=funding,
                            )
                            stress = simulate_portfolio(
                                frames15m,
                                selected_events,
                                rolling_options(
                                    collateral_fraction=collateral_fraction,
                                    leverage=leverage,
                                    account_profit_target=account_profit_target,
                                    time_mode=time_mode,
                                    stress=True,
                                ),
                                start=start,
                                end=selection_end,
                                funding_frames=funding,
                            )
                            metrics = summarize_backtest(normal)
                            stress_metrics = summarize_backtest(stress)
                            passed = bool(
                                metrics["trades"] >= 20
                                and metrics["pf"] >= 1.15
                                and stress_metrics["pf"] >= 1.05
                                and metrics["drawdown"] <= 0.35
                                and metrics["final_equity"] > 0
                            )
                            rows.append(
                                {
                                    "trend_mode": trend_mode,
                                    "direction": direction,
                                    "collateral_fraction": collateral_fraction,
                                    "leverage": leverage,
                                    "account_profit_target": account_profit_target,
                                    "margin_take_profit": account_profit_target / collateral_fraction,
                                    "time_mode": time_mode,
                                    **metrics,
                                    "stress_pf": stress_metrics["pf"],
                                    "stress_final_equity": stress_metrics["final_equity"],
                                    "selection_passed": passed,
                                }
                            )
    matrix = pd.DataFrame(rows).sort_values(
        ["selection_passed", "stress_pf", "pf", "trades", "drawdown"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    matrix.to_csv(args.output_dir / "selection-matrix.csv", index=False)
    eligible = matrix.loc[matrix["trades"] >= 20]
    frozen = (eligible if not eligible.empty else matrix).iloc[0]
    frozen_events = event_sets[str(frozen["trend_mode"])]
    if frozen["direction"] != "both":
        frozen_events = [event for event in frozen_events if event.side == frozen["direction"]]
    option_args = {
        "collateral_fraction": float(frozen["collateral_fraction"]),
        "leverage": float(frozen["leverage"]),
        "account_profit_target": float(frozen["account_profit_target"]),
        "time_mode": str(frozen["time_mode"]),
    }
    holdout = simulate_portfolio(
        frames15m,
        frozen_events,
        rolling_options(**option_args),
        start=selection_end,
        end=end,
        funding_frames=funding,
    )
    holdout_stress = simulate_portfolio(
        frames15m,
        frozen_events,
        rolling_options(**option_args, stress=True),
        start=selection_end,
        end=end,
        funding_frames=funding,
    )
    fixed_cap = simulate_portfolio(
        frames15m,
        frozen_events,
        rolling_options(**option_args, compounding_cap_multiple=1.0),
        start=selection_end,
        end=end,
        funding_frames=funding,
    )
    holdout_metrics = summarize_backtest(holdout)
    holdout_stress_metrics = summarize_backtest(holdout_stress)
    holdout_passed = bool(
        holdout_metrics["trades"] >= 20
        and holdout_metrics["pf"] >= 1.15
        and holdout_stress_metrics["pf"] >= 1.05
        and holdout_metrics["drawdown"] <= 0.35
        and holdout_metrics["final_equity"] > 0
    )
    holdout.equity_curve.to_csv(args.output_dir / "holdout-equity.csv", index=False)
    pd.DataFrame([asdict(trade) for trade in holdout.trades]).to_csv(
        args.output_dir / "holdout-trades.csv", index=False
    )
    result = {
        "status": "diagnostic_passed" if holdout_passed else "research_failed",
        "warning": "Period predates strategy definition but has been viewed in earlier research; not a fresh prospective sample.",
        "frozen": {key: frozen[key].item() if hasattr(frozen[key], "item") else frozen[key] for key in (
            "trend_mode", "direction", "collateral_fraction", "leverage", "account_profit_target", "margin_take_profit", "time_mode"
        )},
        "selection_metrics": {key: frozen[key].item() if hasattr(frozen[key], "item") else frozen[key] for key in (
            "trades", "win_rate", "pf", "stress_pf", "drawdown", "max_consecutive_losses", "final_equity"
        )},
        "holdout_metrics": holdout_metrics,
        "holdout_stress_metrics": holdout_stress_metrics,
        "fixed_100u_sizing_holdout_metrics": summarize_backtest(fixed_cap),
        "holdout_passed": holdout_passed,
    }
    (args.output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "purpose": "post_definition_pseudo_holdout",
                "start": start.isoformat(),
                "selectionEnd": selection_end.isoformat(),
                "end": end.isoformat(),
                "warmupDays": args.warmup_days,
                "pairs": pairs,
                "signalParameters": {
                    mode: asdict(TrendCompressionParameters(trend_mode=mode))
                    for mode in event_sets
                },
                "matrixRows": len(matrix),
                "eventCounts": {mode: len(events) for mode, events in event_sets.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"frozen={result['frozen']} holdout trades={holdout_metrics['trades']} "
        f"pf={holdout_metrics['pf']:.3f} stress_pf={holdout_stress_metrics['pf']:.3f} "
        f"status={result['status']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
