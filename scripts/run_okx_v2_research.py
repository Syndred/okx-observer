#!/usr/bin/env python3
"""Run causal OKX V2 candidate selection, stress, and pseudo-holdout research."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import pandas as pd

from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)
from user_data.strategy_lib.v2_signal_engine import V2Parameters, scan_v2_setups
from user_data.strategy_lib.walk_forward import (
    candidate_grid,
    candidate_score,
    parameter_dict,
    rolling_windows,
)


def load_snapshot(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    instruments = {
        row["symbol"]: row["instId"]
        for row in payload["instruments"]
        if row.get("eligible")
    }
    categories = {
        row["symbol"]: str(row.get("instCategory", ""))
        for row in payload["instruments"]
        if row.get("eligible")
    }
    return instruments, categories


def load_frame(data_dir: Path, instrument: str, timeframe: str) -> pd.DataFrame:
    path = data_dir / f"{instrument}-{timeframe}.feather"
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def load_funding(data_dir: Path, instrument: str) -> pd.DataFrame:
    path = data_dir / f"{instrument}-funding.feather"
    if not path.exists():
        return pd.DataFrame(columns=["date", "rate"])
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def available_trade_pairs(
    data_dir: Path, instruments: dict[str, str], explicit: list[str] | None
) -> list[str]:
    if explicit:
        missing = [pair for pair in explicit if pair not in instruments]
        if missing:
            raise ValueError(f"pairs absent or ineligible in snapshot: {missing}")
        return explicit
    return sorted(
        pair
        for pair, instrument in instruments.items()
        if (data_dir / f"{instrument}-15m.feather").exists()
        and pair not in {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    )


def build_events(
    pair: str,
    category: str,
    frames: dict[str, dict[str, pd.DataFrame]],
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    universe: pd.DataFrame,
    params: V2Parameters,
) -> list[EntryEvent]:
    source = frames[pair]
    signals = scan_v2_setups(
        pair=pair,
        inst_category=category,
        candles15m=source["15m"],
        candles1h=source["1h"],
        candles4h=source["4h"],
        btc4h=btc4h,
        eth4h=eth4h,
        universe_mask=universe,
        params=params,
    )
    pair_mask = universe.loc[universe["pair"] == pair].copy()
    pair_mask["hour"] = pd.to_datetime(pair_mask["date"], utc=True).dt.floor("h")
    ranks = pair_mask.drop_duplicates("hour", keep="last").set_index("hour")["rank"].to_dict()
    events = []
    for index, row in signals.iterrows():
        side = "long" if int(row.get("enter_long", 0)) == 1 else (
            "short" if int(row.get("enter_short", 0)) == 1 else ""
        )
        if not side or index + 1 >= len(signals):
            continue
        next_row = signals.iloc[index + 1]
        date = pd.Timestamp(next_row["date"])
        rank = int(ranks.get(date.floor("h"), 999))
        events.append(
            EntryEvent(
                date=date,
                pair=pair,
                side=side,
                stop_price=float(row["initial_stop_price"]),
                rank=rank,
                category=category,
            )
        )
    return events


def window_specs(start: pd.Timestamp, selection_end: pd.Timestamp) -> tuple[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    windows = rolling_windows(start.date(), selection_end.date())
    if windows:
        return "12m_train_3m_validation", [
            (
                pd.Timestamp(window.validation_start, tz="UTC"),
                pd.Timestamp(window.validation_end, tz="UTC"),
            )
            for window in windows
        ]
    duration = selection_end - start
    validation_start = start + duration * 0.70
    return "short_history_70_30_fallback", [(validation_start, selection_end)]


def json_safe(value: object) -> object:
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and (pd.isna(value) or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def trade_rows(result) -> list[dict[str, object]]:
    return [asdict(trade) for trade in result.trades]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--universe", type=Path, default=Path("user_data/data/okx_v2/universe_top30.feather"))
    parser.add_argument("--output-dir", type=Path, default=Path("user_data/backtest_results/okx-v2"))
    parser.add_argument("--pairs", nargs="*")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    instruments, categories = load_snapshot(args.snapshot)
    pairs = available_trade_pairs(args.data_dir, instruments, args.pairs)
    if not pairs:
        raise SystemExit("No eligible trade pair data found")
    for reference in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        if reference not in instruments:
            raise SystemExit(f"Missing reference in snapshot: {reference}")
    btc4h = load_frame(args.data_dir, instruments["BTC/USDT:USDT"], "4h")
    eth4h = load_frame(args.data_dir, instruments["ETH/USDT:USDT"], "4h")
    frames = {
        pair: {
            timeframe: load_frame(args.data_dir, instruments[pair], timeframe)
            for timeframe in ("15m", "1h", "4h")
        }
        for pair in pairs
    }
    universe = pd.read_feather(args.universe)
    universe["date"] = pd.to_datetime(universe["date"], utc=True)
    base_frames = {pair: timeframes["15m"] for pair, timeframes in frames.items()}
    funding_frames = {
        pair: load_funding(args.data_dir, instruments[pair]) for pair in pairs
    }
    start = min(frame["date"].min() for frame in base_frames.values())
    end = max(frame["date"].max() for frame in base_frames.values()) + pd.Timedelta(minutes=15)
    holdout_days = 2 if args.smoke else 60
    selection_end = end - pd.Timedelta(days=holdout_days)
    if selection_end <= start:
        selection_end = start + (end - start) * 0.70
    window_mode, validation_windows = window_specs(start, selection_end)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    result_rows: list[dict[str, object]] = []
    event_cache: dict[str, list[EntryEvent]] = {}
    candidates = candidate_grid(smoke=args.smoke)
    for candidate_index, params in enumerate(candidates, start=1):
        key = json.dumps(parameter_dict(params), sort_keys=True)
        print(f"candidate {candidate_index}/{len(candidates)} {key}", flush=True)
        events: list[EntryEvent] = []
        for pair in pairs:
            events.extend(
                build_events(
                    pair,
                    categories[pair],
                    frames,
                    btc4h,
                    eth4h,
                    universe,
                    params,
                )
            )
        event_cache[key] = events
        metrics = []
        for window_index, (window_start, window_end) in enumerate(validation_windows, start=1):
            normal = simulate_portfolio(
                base_frames,
                events,
                BacktestOptions(),
                start=window_start,
                end=window_end,
                funding_frames=funding_frames,
            )
            stressed = simulate_portfolio(
                base_frames,
                events,
                BacktestOptions(fee_rate=0.001, slippage_rate=0.001),
                start=window_start,
                end=window_end,
                funding_frames=funding_frames,
            )
            normal_metrics = summarize_backtest(normal)
            stress_metrics = summarize_backtest(stressed)
            normal_metrics["stress_pf"] = stress_metrics["pf"]
            metrics.append(normal_metrics)
            write_json(
                output_dir / "windows" / f"candidate-{candidate_index:03d}-window-{window_index:02d}.json",
                {
                    "parameters": parameter_dict(params),
                    "start": window_start,
                    "end": window_end,
                    "metrics": normal_metrics,
                    "stress_metrics": stress_metrics,
                },
            )
        scored = candidate_score(metrics)
        result_rows.append(
            {
                "candidate": candidate_index,
                **parameter_dict(params),
                **asdict(scored),
                "failed_gates": ",".join(scored.failed_gates),
                "events": len(events),
            }
        )
    candidates_frame = pd.DataFrame(result_rows).sort_values(
        ["passed", "score", "candidate"], ascending=[False, False, True]
    )
    candidates_frame.to_csv(output_dir / "candidate-results.csv", index=False)
    best_row = candidates_frame.iloc[0]
    best_index = int(best_row["candidate"])
    best_params = candidates[best_index - 1]
    best_key = json.dumps(parameter_dict(best_params), sort_keys=True)
    best_events = event_cache[best_key]
    passed = bool(best_row["passed"])
    frozen = {
        "status": "passed" if passed else "research_failed",
        "window_mode": window_mode,
        "data_start": start,
        "selection_end": selection_end,
        "pseudo_holdout_start": selection_end,
        "data_end": end,
        "pairs": pairs,
        "candidate": best_index,
        "parameters": parameter_dict(best_params),
        "selection_score": {key: best_row[key] for key in (
            "score", "profit_factor", "max_drawdown", "trades", "worst_window_pf", "stress_pf", "failed_gates"
        )},
    }
    write_json(output_dir / "frozen-candidate.json", frozen)
    manifest_name = "selected-candidate.json" if passed else "research-failed.json"
    write_json(output_dir / manifest_name, frozen)

    variant_rows = []
    for exit_mode in ("hybrid", "fixed3", "fixed5"):
        for time_mode in ("default", "none", "24h"):
            for leverage in (1.0, 3.0, 5.0):
                for cap in (None, 2.0):
                    options = BacktestOptions(
                        leverage=leverage,
                        exit_mode=exit_mode,
                        time_mode=time_mode,
                        compounding_cap_multiple=cap,
                    )
                    result = simulate_portfolio(
                        base_frames,
                        best_events,
                        options,
                        start=selection_end,
                        end=end,
                        funding_frames=funding_frames,
                    )
                    variant_rows.append(
                        {
                            "exit_mode": exit_mode,
                            "time_mode": time_mode,
                            "leverage": leverage,
                            "cap_multiple": cap,
                            **summarize_backtest(result),
                        }
                    )
    variants = pd.DataFrame(variant_rows).sort_values(
        ["pf", "drawdown", "final_equity"], ascending=[False, True, False]
    )
    variants.to_csv(output_dir / "pseudo-holdout-variants.csv", index=False)
    default_result = simulate_portfolio(
        base_frames,
        best_events,
        BacktestOptions(),
        start=selection_end,
        end=end,
        funding_frames=funding_frames,
    )
    pd.DataFrame(trade_rows(default_result)).to_csv(output_dir / "pseudo-holdout-trades.csv", index=False)
    default_result.equity_curve.to_csv(output_dir / "pseudo-holdout-equity.csv", index=False)
    write_json(
        output_dir / "pseudo-holdout-metrics.json",
        {"metrics": summarize_backtest(default_result)},
    )
    write_json(
        output_dir / "run-manifest.json",
        {
            "argv": sys.argv,
            "smoke": args.smoke,
            "candidate_count": len(candidates),
            "window_count": len(validation_windows),
            "window_mode": window_mode,
            "pairs": pairs,
            "cost_assumptions": {
                "normal_fee_per_side": 0.0005,
                "normal_slippage_per_side": 0.0005,
                "stress_fee_per_side": 0.001,
                "stress_slippage_per_side": 0.001,
            },
        },
    )
    print(f"saved research to {output_dir}; status={frozen['status']}")


if __name__ == "__main__":
    main()
