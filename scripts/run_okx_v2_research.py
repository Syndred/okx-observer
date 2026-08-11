#!/usr/bin/env python3
"""Run causal OKX V2 candidate selection, stress, and pseudo-holdout research."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
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
from user_data.strategy_lib.dual_ma_signal_engine import DualMaParameters, scan_dual_ma_setups
from user_data.strategy_lib.six_ma_mtf_signal_engine import (
    SixMaMtfParameters,
    scan_six_ma_mtf_setups,
)
from user_data.strategy_lib.walk_forward import (
    candidate_grid,
    candidate_score,
    dual_ma_candidate_grid,
    parameter_dict,
    rolling_windows,
    six_ma_mtf_candidate_grid,
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


def parse_utc_timestamp(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    return pd.Timestamp(value, tz="UTC")


def clip_frame(
    frame: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None
) -> pd.DataFrame:
    if frame.empty:
        return frame
    selected = frame
    if start is not None:
        selected = selected.loc[selected["date"] >= start]
    if end is not None:
        selected = selected.loc[selected["date"] < end]
    return selected.reset_index(drop=True)


def event_density_rows(
    events: list[EntryEvent], start: pd.Timestamp, end: pd.Timestamp
) -> list[dict[str, object]]:
    in_period = [
        event
        for event in events
        if start <= pd.Timestamp(event.date) < end
    ]
    if not in_period:
        return []
    frame = pd.DataFrame(
        {
            "date": [pd.Timestamp(event.date) for event in in_period],
            "pair": [event.pair for event in in_period],
            "side": [event.side for event in in_period],
        }
    )
    frame["month"] = frame["date"].dt.strftime("%Y-%m")
    rows = []
    for month, group in frame.groupby("month", sort=True):
        rows.append(
            {
                "month": month,
                "events": int(len(group)),
                "pairs": int(group["pair"].nunique()),
                "long": int((group["side"] == "long").sum()),
                "short": int((group["side"] == "short").sum()),
            }
        )
    return rows


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
    params: V2Parameters | DualMaParameters | SixMaMtfParameters,
    signal_mode: str,
) -> list[EntryEvent]:
    source = frames[pair]
    if signal_mode == "six_ma_mtf":
        signals = scan_six_ma_mtf_setups(
            pair=pair,
            inst_category=category,
            candles15m=source["15m"],
            candles4h=source["4h"],
            btc4h=btc4h,
            eth4h=eth4h,
            universe_mask=universe,
            params=params,  # type: ignore[arg-type]
        )
    elif signal_mode == "dual_ma":
        signals = scan_dual_ma_setups(
            pair=pair,
            inst_category=category,
            candles15m=source["15m"],
            candles1h=source["1h"],
            candles4h=source["4h"],
            btc4h=btc4h,
            eth4h=eth4h,
            universe_mask=universe,
            params=params,  # type: ignore[arg-type]
        )
    else:
        signals = scan_v2_setups(
            pair=pair,
            inst_category=category,
            candles15m=source["15m"],
            candles1h=source["1h"],
            candles4h=source["4h"],
            btc4h=btc4h,
            eth4h=eth4h,
            universe_mask=universe,
            params=params,  # type: ignore[arg-type]
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
        if rank == 999:
            continue
        entry_price = float(next_row["open"])
        stop_price = float(row["initial_stop_price"])
        correctly_sided = (
            stop_price < entry_price if side == "long" else stop_price > entry_price
        )
        if not correctly_sided or not pd.notna(stop_price):
            continue
        events.append(
            EntryEvent(
                date=date,
                pair=pair,
                side=side,
                stop_price=stop_price,
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


def funding_coverage_rows(
    pairs: list[str],
    base_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[dict[str, object]]:
    rows = []
    for pair in pairs:
        candles = base_frames[pair]
        pair_start = max(start, pd.Timestamp(candles["date"].min()))
        pair_end = min(end, pd.Timestamp(candles["date"].max()))
        funding = funding_frames.get(pair, pd.DataFrame())
        first = pd.NaT if funding.empty else pd.Timestamp(funding["date"].min())
        last = pd.NaT if funding.empty else pd.Timestamp(funding["date"].max())
        complete = bool(
            pd.notna(first)
            and pd.notna(last)
            and first <= pair_start + pd.Timedelta(hours=12)
            and last >= pair_end - pd.Timedelta(hours=12)
        )
        rows.append(
            {
                "pair": pair,
                "period_start": pair_start,
                "period_end": pair_end,
                "funding_start": first,
                "funding_end": last,
                "complete": complete,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--universe", type=Path, default=Path("user_data/data/okx_v2/universe_top30.feather"))
    parser.add_argument("--output-dir", type=Path, default=Path("user_data/backtest_results/okx-v2"))
    parser.add_argument("--pairs", nargs="*")
    parser.add_argument("--start", type=str, default=None, help="UTC research start, e.g. 2026-02-10")
    parser.add_argument("--end", type=str, default=None, help="UTC research end exclusive, e.g. 2026-08-11")
    parser.add_argument("--holdout-days", type=int, default=None)
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=40,
        help="Keep candles before --start for EMA/MA warm-up when date-clipped",
    )
    parser.add_argument(
        "--purpose",
        type=str,
        default="formal",
        help="Label for run-manifest; use half_year_diagnose for short samples",
    )
    parser.add_argument(
        "--signal-mode",
        choices=("v2", "dual_ma", "six_ma_mtf"),
        default="v2",
        help=(
            "v2=1H compression/pullback; dual_ma=dual EMA pullback; "
            "six_ma_mtf=4H compression breakout plus 15m nested compression breakout"
        ),
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--exhaustive", action="store_true")
    parser.add_argument("--signal-workers", type=int, default=4)
    parser.add_argument("--imputed-funding-rate", type=float, default=0.0001)
    parser.add_argument("--stress-imputed-funding-rate", type=float, default=0.0002)
    args = parser.parse_args()
    if args.signal_workers < 1 or args.signal_workers > 8:
        raise SystemExit("signal-workers must be between 1 and 8")
    if args.warmup_days < 0:
        raise SystemExit("warmup-days must be >= 0")
    requested_start = parse_utc_timestamp(args.start)
    requested_end = parse_utc_timestamp(args.end)
    if requested_start is not None and requested_end is not None and requested_start >= requested_end:
        raise SystemExit("--start must be earlier than --end")
    instruments, categories = load_snapshot(args.snapshot)
    pairs = available_trade_pairs(args.data_dir, instruments, args.pairs)
    if not pairs:
        raise SystemExit("No eligible trade pair data found")
    for reference in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        if reference not in instruments:
            raise SystemExit(f"Missing reference in snapshot: {reference}")
    load_start = (
        None
        if requested_start is None
        else requested_start - pd.Timedelta(days=args.warmup_days)
    )
    btc4h = clip_frame(
        load_frame(args.data_dir, instruments["BTC/USDT:USDT"], "4h"),
        load_start,
        requested_end,
    )
    eth4h = clip_frame(
        load_frame(args.data_dir, instruments["ETH/USDT:USDT"], "4h"),
        load_start,
        requested_end,
    )
    frames = {
        pair: {
            timeframe: clip_frame(
                load_frame(args.data_dir, instruments[pair], timeframe),
                load_start,
                requested_end,
            )
            for timeframe in ("15m", "1h", "4h")
        }
        for pair in pairs
    }
    universe = pd.read_feather(args.universe)
    universe["date"] = pd.to_datetime(universe["date"], utc=True)
    universe = clip_frame(universe, load_start, requested_end)
    base_frames = {pair: timeframes["15m"] for pair, timeframes in frames.items()}
    funding_frames = {
        pair: clip_frame(
            load_funding(args.data_dir, instruments[pair]), load_start, requested_end
        )
        for pair in pairs
    }
    data_start = min(frame["date"].min() for frame in base_frames.values() if not frame.empty)
    data_end = (
        max(frame["date"].max() for frame in base_frames.values() if not frame.empty)
        + pd.Timedelta(minutes=15)
    )
    start = requested_start if requested_start is not None else data_start
    end = requested_end if requested_end is not None else data_end
    if start < data_start:
        start = data_start
    if end > data_end:
        end = data_end
    if args.holdout_days is not None:
        holdout_days = args.holdout_days
    else:
        holdout_days = 2 if args.smoke else 60
    if holdout_days < 0:
        raise SystemExit("holdout-days must be >= 0")
    selection_end = end - pd.Timedelta(days=holdout_days)
    if selection_end <= start:
        selection_end = start + (end - start) * 0.70
    window_mode, validation_windows = window_specs(start, selection_end)
    print(
        f"research period {start} -> {end}; selection_end {selection_end}; "
        f"window_mode={window_mode}; purpose={args.purpose}",
        flush=True,
    )
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    result_rows: list[dict[str, object]] = []
    event_cache: dict[str, list[EntryEvent]] = {}
    if args.signal_mode == "dual_ma":
        candidates = dual_ma_candidate_grid(
            smoke=args.smoke, exhaustive=args.exhaustive
        )
    elif args.signal_mode == "six_ma_mtf":
        candidates = six_ma_mtf_candidate_grid(
            smoke=args.smoke, exhaustive=args.exhaustive
        )
    else:
        candidates = candidate_grid(
            smoke=args.smoke, exhaustive=args.exhaustive
        )
    with ThreadPoolExecutor(max_workers=args.signal_workers) as signal_pool:
        for candidate_index, params in enumerate(candidates, start=1):
            key = json.dumps(parameter_dict(params), sort_keys=True)
            print(f"candidate {candidate_index}/{len(candidates)} {key}", flush=True)

            def scan_pair(pair: str, active_params=params) -> list[EntryEvent]:
                return build_events(
                    pair,
                    categories[pair],
                    frames,
                    btc4h,
                    eth4h,
                    universe,
                    active_params,
                    args.signal_mode,
                )

            events = [
                event
                for pair_events in signal_pool.map(scan_pair, pairs)
                for event in pair_events
            ]
            event_cache[key] = events
            metrics = []
            pyramid_options = {}
            if isinstance(params, DualMaParameters):
                pyramid_options = {
                    "pyramid_enabled": params.pyramid_enabled,
                    "pyramid_trigger_r": params.pyramid_trigger_r,
                    "pyramid_risk_fraction": params.pyramid_risk_fraction,
                    "max_pyramids": params.max_pyramids,
                }
            for window_index, (window_start, window_end) in enumerate(validation_windows, start=1):
                normal = simulate_portfolio(
                    base_frames,
                    events,
                    BacktestOptions(
                        missing_funding_rate_per_8h=args.imputed_funding_rate,
                        **pyramid_options,
                    ),
                    start=window_start,
                    end=window_end,
                    funding_frames=funding_frames,
                )
                stressed = simulate_portfolio(
                    base_frames,
                    events,
                    BacktestOptions(
                        fee_rate=0.001,
                        slippage_rate=0.001,
                        missing_funding_rate_per_8h=args.stress_imputed_funding_rate,
                        **pyramid_options,
                    ),
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
                    "failed_gate_count": len(scored.failed_gates),
                    "failed_gates": ",".join(scored.failed_gates),
                    "events": len(events),
                }
            )
    candidates_frame = pd.DataFrame(result_rows).sort_values(
        ["passed", "trades", "failed_gate_count", "score", "candidate"],
        ascending=[False, False, True, False, True],
    )
    candidates_frame.to_csv(output_dir / "candidate-results.csv", index=False)
    best_row = candidates_frame.iloc[0]
    best_index = int(best_row["candidate"])
    best_params = candidates[best_index - 1]
    best_key = json.dumps(parameter_dict(best_params), sort_keys=True)
    best_events = event_cache[best_key]
    holdout_event_pairs = sorted(
        {
            event.pair
            for event in best_events
            if selection_end <= pd.Timestamp(event.date) < end
        }
    )
    coverage = pd.DataFrame(
        funding_coverage_rows(
            holdout_event_pairs,
            base_frames,
            funding_frames,
            selection_end,
            end,
        )
    )
    coverage.to_csv(output_dir / "pseudo-holdout-funding-coverage.csv", index=False)
    funding_complete = bool(not coverage.empty and coverage["complete"].all())
    passed = bool(best_row["passed"])
    density = pd.DataFrame(event_density_rows(best_events, start, end))
    density.to_csv(output_dir / "event-density-by-month.csv", index=False)
    period_events = int(density["events"].sum()) if not density.empty else 0
    frozen = {
        "status": "passed" if passed else "research_failed",
        "purpose": args.purpose,
        "signal_mode": args.signal_mode,
        "live_claim_allowed": args.purpose == "formal" and passed,
        "window_mode": window_mode,
        "data_start": start,
        "selection_end": selection_end,
        "pseudo_holdout_start": selection_end,
        "data_end": end,
        "holdout_days": holdout_days,
        "warmup_days": args.warmup_days,
        "period_events": period_events,
        "pairs": pairs,
        "candidate": best_index,
        "parameters": parameter_dict(best_params),
        "funding_model": {
            "observed_history_limit": "OKX public endpoint maximum three months",
            "selection_missing_adverse_rate_per_8h": args.imputed_funding_rate,
            "stress_missing_adverse_rate_per_8h": args.stress_imputed_funding_rate,
            "pseudo_holdout_event_pair_coverage_complete": funding_complete,
        },
        "selection_score": {key: best_row[key] for key in (
            "score", "profit_factor", "max_drawdown", "trades", "worst_window_pf", "stress_pf", "failed_gates"
        )},
    }
    best_pyramid = {}
    if isinstance(best_params, DualMaParameters):
        best_pyramid = {
            "pyramid_enabled": best_params.pyramid_enabled,
            "pyramid_trigger_r": best_params.pyramid_trigger_r,
            "pyramid_risk_fraction": best_params.pyramid_risk_fraction,
            "max_pyramids": best_params.max_pyramids,
        }
    variant_rows = []
    risk_profiles = (
        ("conservative", 0.005, 0.015),
        ("default", 0.0075, 0.020),
        ("aggressive", 0.010, 0.030),
    )
    default_risk = risk_profiles[1]
    variant_specs = {
        (exit_mode, time_mode, 3.0, default_risk, None)
        for exit_mode in ("hybrid", "fixed3", "fixed5")
        for time_mode in ("default", "none", "24h")
    }
    variant_specs.update(
        {("hybrid", "default", leverage, default_risk, None) for leverage in (3.0, 5.0, 10.0)}
    )
    variant_specs.update(
        {("hybrid", "default", 3.0, profile, None) for profile in risk_profiles}
    )
    variant_specs.add(("hybrid", "default", 3.0, default_risk, 2.0))
    for exit_mode, time_mode, leverage, risk_profile, cap in sorted(
        variant_specs, key=str
    ):
        risk_name, trade_risk, portfolio_risk = risk_profile
        options = BacktestOptions(
            leverage=leverage,
            exit_mode=exit_mode,
            time_mode=time_mode,
            compounding_cap_multiple=cap,
            normal_trade_risk=trade_risk,
            reduced_trade_risk=0.005,
            max_portfolio_risk=portfolio_risk,
            **best_pyramid,
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
                "risk_profile": risk_name,
                "trade_risk": trade_risk,
                "portfolio_risk": portfolio_risk,
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
        BacktestOptions(**best_pyramid),
        start=selection_end,
        end=end,
        funding_frames=funding_frames,
    )
    stress_result = simulate_portfolio(
        base_frames,
        best_events,
        BacktestOptions(
            fee_rate=0.001,
            slippage_rate=0.001,
            missing_funding_rate_per_8h=args.stress_imputed_funding_rate,
            **best_pyramid,
        ),
        start=selection_end,
        end=end,
        funding_frames=funding_frames,
    )
    holdout_metrics = summarize_backtest(default_result)
    holdout_stress_metrics = summarize_backtest(stress_result)
    holdout_metrics["stress_pf"] = holdout_stress_metrics["pf"]
    holdout_passed = bool(
        holdout_metrics["trades"] >= 300
        and holdout_metrics["pf"] >= 1.15
        and holdout_metrics["drawdown"] <= 0.35
        and holdout_metrics["stress_pf"] >= 1.05
        and holdout_metrics["final_equity"] > 0
        and funding_complete
    )
    overall_passed = passed and holdout_passed
    live_claim_allowed = args.purpose == "formal" and overall_passed
    frozen.update(
        {
            "status": "passed" if overall_passed else "research_failed",
            "selection_passed": passed,
            "holdout_passed": holdout_passed,
            "live_claim_allowed": live_claim_allowed,
            "holdout_score": {
                "profit_factor": holdout_metrics["pf"],
                "max_drawdown": holdout_metrics["drawdown"],
                "trades": holdout_metrics["trades"],
                "stress_pf": holdout_metrics["stress_pf"],
                "funding_complete": funding_complete,
            },
        }
    )
    pd.DataFrame(trade_rows(default_result)).to_csv(output_dir / "pseudo-holdout-trades.csv", index=False)
    default_result.equity_curve.to_csv(output_dir / "pseudo-holdout-equity.csv", index=False)
    write_json(
        output_dir / "pseudo-holdout-metrics.json",
        {"metrics": holdout_metrics, "stress_metrics": holdout_stress_metrics},
    )
    write_json(output_dir / "frozen-candidate.json", frozen)
    manifest_name = "selected-candidate.json" if overall_passed else "research-failed.json"
    write_json(output_dir / manifest_name, frozen)
    write_json(
        output_dir / "run-manifest.json",
        {
            "argv": sys.argv,
            "purpose": args.purpose,
            "signal_mode": args.signal_mode,
            "live_claim_allowed": live_claim_allowed,
            "selection_passed": passed,
            "holdout_passed": holdout_passed,
            "smoke": args.smoke,
            "candidate_count": len(candidates),
            "search_mode": f"exhaustive_{len(candidates)}" if args.exhaustive else (
                "smoke" if args.smoke else "balanced_32"
            ),
            "window_count": len(validation_windows),
            "window_mode": window_mode,
            "data_start": start,
            "selection_end": selection_end,
            "data_end": end,
            "holdout_days": holdout_days,
            "warmup_days": args.warmup_days,
            "period_events": period_events,
            "pairs": pairs,
            "cost_assumptions": {
                "normal_fee_per_side": 0.0005,
                "normal_slippage_per_side": 0.0005,
                "stress_fee_per_side": 0.001,
                "stress_slippage_per_side": 0.001,
                "selection_missing_funding_adverse_per_8h": args.imputed_funding_rate,
                "stress_missing_funding_adverse_per_8h": args.stress_imputed_funding_rate,
            },
        },
    )
    print(f"saved research to {output_dir}; status={frozen['status']}")


if __name__ == "__main__":
    main()
