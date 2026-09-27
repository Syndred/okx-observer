#!/usr/bin/env python3
"""Walk-forward train a fixed RSI(2) quality filter; never sends live orders."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from scripts.run_cross_sectional_momentum_research import (
    END,
    SPLIT_A,
    SPLIT_B,
    TRAIN_START,
    _events_csv,
    _label_scenarios,
    _portfolio_scenarios,
    _qualifies_labels,
    _qualifies_portfolio,
)
from scripts.run_jev_ma_research import fingerprint
from scripts.run_jev_profit_research import (
    AUDIT_START,
    AUDIT_SYMBOLS,
    load_dataset,
)
from scripts.run_okx_v2_research import write_json
from user_data.strategy_lib.rsi2_trend_reversion import (
    DEV_SYMBOLS,
    FEATURE_NAMES,
    RSI2TrendReversionParameters,
    build_event_features,
    generate_events,
)
from user_data.strategy_lib.scalp_paths import evaluate_events
from user_data.strategy_lib.temporal_logistic import (
    fit_logistic_regression,
    predict_probability,
)
from user_data.strategy_lib.v2_backtester import EntryEvent

TARGET_R = 1.5
HOLD_MINUTES = 120
HOLD = pd.Timedelta(7_200_000_000_000, unit="ns")
PROBABILITY_THRESHOLD = 0.50
PARAMS = RSI2TrendReversionParameters()
PROTOCOL = ROOT / "docs/research/JEV_RSI2_WALK_FORWARD_FILTER_PROTOCOL.md"
FOLDS = (
    (pd.Timestamp("2026-04-01", tz="UTC"),
     pd.Timestamp("2026-04-01", tz="UTC"),
     pd.Timestamp("2026-05-01", tz="UTC")),
    (pd.Timestamp("2026-05-01", tz="UTC"),
     pd.Timestamp("2026-05-01", tz="UTC"),
     SPLIT_A),
)


def _key(date_value, pair: str, side: str) -> tuple[int, str, str]:
    stamp = pd.Timestamp(date_value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return int(stamp.value), pair, side


def _complete_events(events: list[EntryEvent], start: pd.Timestamp,
                     end: pd.Timestamp) -> list[EntryEvent]:
    return [event for event in events
            if start <= event.date and event.date + HOLD <= end]


def _score_events(frames, funding, events, *, name: str, out: Path) -> pd.DataFrame:
    return evaluate_events(
        frames, events, TARGET_R, HOLD_MINUTES,
        fee_rate=.0005,
        slippage_rate=.0005,
        funding_rate_per_8h=.0001,
        funding_frames=funding,
    )


def _labeled_matrix(frames, funding, events, out: Path):
    selected = _complete_events(events, TRAIN_START, SPLIT_A)
    labels = _score_events(frames, funding, selected, name="training", out=out)
    event_map = {_key(event.date, event.pair, event.side): event for event in selected}
    aligned_events = []
    for row in labels.itertuples(index=False):
        key = _key(row.date, row.pair, row.side)
        if key not in event_map:
            raise ValueError("label row has no matching RSI(2) event")
        aligned_events.append(event_map[key])
    features = build_event_features(frames, aligned_events, DEV_SYMBOLS)
    outcomes = labels["net_return"].to_numpy(dtype=float)
    return labels, aligned_events, features, outcomes


def _walk_forward_predictions(labels: pd.DataFrame, events: list[EntryEvent],
                              features: np.ndarray, outcomes: np.ndarray):
    dates = pd.to_datetime(labels["date"], utc=True)
    labels_binary = (outcomes > 0).astype(float)
    probabilities = np.full(len(labels), np.nan, dtype=float)
    fold_models = []
    audit_rows: list[dict[str, object]] = []
    for fit_end, validation_start, validation_end in FOLDS:
        training_mask = (dates + HOLD <= fit_end).to_numpy()
        validation_mask = (
            (dates >= validation_start) & (dates + HOLD <= validation_end)
        ).to_numpy()
        train_indices = np.flatnonzero(training_mask)
        validation_indices = np.flatnonzero(validation_mask)
        model = fit_logistic_regression(
            features[train_indices], labels_binary[train_indices],
            l2=.1, learning_rate=.1, steps=500,
        )
        fold_probabilities = predict_probability(model, features[validation_indices])
        probabilities[validation_indices] = fold_probabilities
        fold_models.append({
            "fit_end": fit_end.isoformat(),
            "validation_start": validation_start.isoformat(),
            "validation_end": validation_end.isoformat(),
            "training_rows": int(len(train_indices)),
            "validation_rows": int(len(validation_indices)),
            "model": model,
        })
        for index, probability in zip(validation_indices, fold_probabilities):
            event = events[index]
            audit_rows.append({
                "date": event.date,
                "pair": event.pair,
                "side": event.side,
                "fold_validation_start": validation_start,
                "predicted_probability": float(probability),
                "realized_net_return": float(outcomes[index]),
                "realized_win": int(labels_binary[index]),
            })
    return probabilities, fold_models, pd.DataFrame(audit_rows)


def _apply_model(frames, events: list[EntryEvent], model):
    if not events:
        return [], pd.DataFrame(columns=["date", "pair", "side", "predicted_probability"])
    features = build_event_features(frames, events, DEV_SYMBOLS)
    probabilities = predict_probability(model, features)
    selected = []
    rows = []
    for event, probability in zip(events, probabilities):
        rows.append({
            "date": event.date, "pair": event.pair, "side": event.side,
            "stop_price": event.stop_price,
            "predicted_probability": float(probability),
        })
        if probability >= PROBABILITY_THRESHOLD:
            selected.append(event)
    return selected, pd.DataFrame(rows)


def run(args: argparse.Namespace) -> None:
    out: Path = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "scripts/run_cross_sectional_momentum_research.py",
        ROOT / "scripts/run_jev_profit_research.py",
        ROOT / "user_data/strategy_lib/rsi2_trend_reversion.py",
        ROOT / "user_data/strategy_lib/temporal_logistic.py",
        ROOT / "user_data/strategy_lib/scalp_paths.py",
        ROOT / "user_data/strategy_lib/v2_signal_engine.py",
        ROOT / "user_data/strategy_lib/v2_backtester.py",
    ]
    manifest: dict[str, object] = {
        "status": "training_walk_forward",
        "candidate": "walk-forward L2 logistic filter on RSI(2) trend-reversion events",
        "base_parameters": asdict(PARAMS),
        "feature_names": FEATURE_NAMES,
        "classifier": {
            "family": "numpy_binary_logistic_l2",
            "l2": .1,
            "learning_rate": .1,
            "steps": 500,
            "probability_threshold": PROBABILITY_THRESHOLD,
        },
        "target_r": TARGET_R,
        "hold_minutes": HOLD_MINUTES,
        "development_symbols": DEV_SYMBOLS,
        "audit_symbols": AUDIT_SYMBOLS,
        "train": [TRAIN_START.isoformat(), SPLIT_A.isoformat()],
        "development_a": [SPLIT_A.isoformat(), SPLIT_B.isoformat()],
        "development_b": [SPLIT_B.isoformat(), END.isoformat()],
        "heldout_prices_opened": False,
        "live_claim_allowed": False,
        "protocol_sha256": fingerprint(PROTOCOL),
        "code_sha256": {str(path.relative_to(ROOT)): fingerprint(path) for path in code_paths},
    }
    write_json(out / "manifest.json", manifest)
    frames, funding, hashes = load_dataset(args.data_dir, DEV_SYMBOLS, TRAIN_START)
    manifest["development_data_sha256"] = hashes
    write_json(out / "manifest.json", manifest)
    base_events = [event for pair, frame in frames.items()
                   for event in generate_events(pair, frame, PARAMS)]
    base_events.sort(key=lambda event: (event.date, event.pair, event.side))
    train_labels, train_events, train_features, outcomes = _labeled_matrix(
        frames, funding, base_events, out,
    )
    manifest["base_training_events"] = int(len(train_events))
    labels_binary = (outcomes > 0).astype(float)
    if not np.isfinite(train_features).all() or not len(train_events):
        raise ValueError("training feature matrix is empty or non-finite")

    oof_probabilities, fold_models, oof_predictions = _walk_forward_predictions(
        train_labels, train_events, train_features, outcomes,
    )
    oof_predictions.to_csv(out / "training-oof-predictions.csv", index=False)
    write_json(out / "walk-forward-models.json", {
        "threshold": PROBABILITY_THRESHOLD,
        "feature_names": FEATURE_NAMES,
        "folds": fold_models,
    })
    oof_mask = np.isfinite(oof_probabilities)
    oof_selected = [event for event, score in zip(train_events, oof_probabilities)
                    if np.isfinite(score) and score >= PROBABILITY_THRESHOLD]
    oof_metrics = _label_scenarios(
        frames, funding, oof_selected, TRAIN_START, SPLIT_A, out,
        "training-walk-forward", hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "training-walk-forward-metrics.json", oof_metrics)
    manifest["walk_forward"] = {
        "oof_rows": int(oof_mask.sum()),
        "selected_events": int(len(oof_selected)),
        "folds": [{key: value for key, value in fold.items() if key != "model"}
                  for fold in fold_models],
    }
    if not _qualifies_labels(oof_metrics, 80):
        manifest.update(
            status="no_qualified_training_candidate",
            reason="Walk-forward out-of-time training labels failed frozen sample, net-win, PF, return or stress gates; development and reserve were not scored",
        )
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    final_model = fit_logistic_regression(
        train_features, labels_binary, l2=.1, learning_rate=.1, steps=500,
    )
    write_json(out / "final-training-model.json", {
        "feature_names": FEATURE_NAMES,
        "probability_threshold": PROBABILITY_THRESHOLD,
        "training_window": [TRAIN_START.isoformat(), SPLIT_A.isoformat()],
        "model": final_model,
    })
    manifest["status"] = "development_a"
    write_json(out / "manifest.json", manifest)

    development_base = _complete_events(base_events, SPLIT_A, END)
    development_events, development_scores = _apply_model(frames, development_base, final_model)
    development_scores.to_csv(out / "development-scored-events.csv", index=False)
    a_events = _complete_events(development_events, SPLIT_A, SPLIT_B)
    a_metrics = _label_scenarios(
        frames, funding, a_events, SPLIT_A, SPLIT_B, out, "development-a",
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-a-label-metrics.json", a_metrics)
    if not _qualifies_labels(a_metrics, 30):
        manifest.update(status="development_a_failed",
                        reason="Development A failed frozen gates; development B and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_b"
    write_json(out / "manifest.json", manifest)
    b_events = _complete_events(development_events, SPLIT_B, END)
    b_metrics = _label_scenarios(
        frames, funding, b_events, SPLIT_B, END, out, "development-b",
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-b-label-metrics.json", b_metrics)
    if not (_qualifies_labels(b_metrics, 30)
            and a_metrics["regular"]["n"] + b_metrics["regular"]["n"] >= 100):
        manifest.update(status="development_b_failed",
                        reason="Development B or combined sample failed; portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_a"
    write_json(out / "manifest.json", manifest)
    portfolio_a = _portfolio_scenarios(
        frames, funding, a_events, SPLIT_A, SPLIT_B,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-a-portfolio-metrics.json", portfolio_a)
    if not _qualifies_portfolio(portfolio_a):
        manifest.update(status="development_a_portfolio_failed",
                        reason="Development A portfolio failed frozen risk or return gates; development B portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_b"
    write_json(out / "manifest.json", manifest)
    portfolio_b = _portfolio_scenarios(
        frames, funding, b_events, SPLIT_B, END,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-b-portfolio-metrics.json", portfolio_b)
    if not (_qualifies_portfolio(portfolio_b)
            and portfolio_a["regular"]["trades"] + portfolio_b["regular"]["trades"] >= 100):
        manifest.update(status="development_b_portfolio_failed",
                        reason="Development B portfolio or combined sample failed; reserve was not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "reserve_validation"
    write_json(out / "manifest.json", manifest)
    manifest["heldout_prices_opened"] = True
    write_json(out / "manifest.json", manifest)
    audit_frames, audit_funding, audit_hashes = load_dataset(
        args.holdout_dir, AUDIT_SYMBOLS, AUDIT_START,
    )
    manifest["audit_data_sha256"] = audit_hashes
    write_json(out / "manifest.json", manifest)
    audit_base = [event for pair, frame in audit_frames.items()
                  for event in generate_events(pair, frame, PARAMS)]
    audit_base.sort(key=lambda event: (event.date, event.pair, event.side))
    audit_base = _complete_events(audit_base, AUDIT_START, END)
    audit_events, audit_scores = _apply_model(audit_frames, audit_base, final_model)
    audit_scores.to_csv(out / "reserve-scored-events.csv", index=False)
    audit_metrics = _label_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END, out, "reserve",
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "reserve-label-metrics.json", audit_metrics)
    audit_portfolio = _portfolio_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "reserve-portfolio-metrics.json", audit_portfolio)
    regular = audit_metrics["regular"]
    passed = (
        regular["n"] >= 100 and regular["total_net_return"] > 0
        and regular["pf"] is not None and regular["pf"] >= 1.2
        and audit_portfolio["regular"]["trades"] >= 100
        and audit_portfolio["regular"]["final_equity"] > 100
        and audit_portfolio["regular"]["drawdown"] <= .20
        and all(audit_metrics[key]["total_net_return"] >= 0
                and ((audit_metrics[key]["pf"] is not None and audit_metrics[key]["pf"] >= 1.0)
                     or audit_metrics[key]["pf_unbounded"])
                for key in ("cost_stress", "strict_stress"))
        and all(audit_portfolio[key]["final_equity"] >= 100
                and audit_portfolio[key]["pf"] is not None
                and audit_portfolio[key]["pf"] >= 1.0
                for key in ("cost_stress", "strict_stress"))
    )
    manifest.update(
        status=("historic_validation_passed_requires_forward_simulation"
                if passed else "reserve_validation_failed"),
        reserve_passed=bool(passed),
        reason=None if passed else "Frozen reserve gates failed; do not retune on these results",
    )
    write_json(out / "manifest.json", manifest)
    print(manifest["status"], flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=ROOT / "user_data/data/okx_scalp_long")
    parser.add_argument("--holdout-dir", type=Path,
                        default=ROOT / "user_data/data/okx_profit_holdout")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "user_data/backtest_results/rsi2-walk-forward-filter-20260927-train-only")
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        manifest_path = args.output_dir / "manifest.json"
        if manifest_path.is_file():
            import json
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(status="failed", error_type=type(error).__name__)
            write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
