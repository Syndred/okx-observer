#!/usr/bin/env python3
"""Frozen training-first evaluation of extreme-funding contrarian entries."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_cross_sectional_momentum_research import (
    END,
    SPLIT_A,
    SPLIT_B,
    TRAIN_START,
    _label_scenarios,
    _portfolio_scenarios,
    _qualifies_labels,
    _qualifies_portfolio,
)
from scripts.run_jev_ma_research import fingerprint
from scripts.run_jev_profit_research import (
    AUDIT_START,
    AUDIT_SYMBOLS,
    DEV_SYMBOLS,
    load_dataset,
)
from scripts.run_okx_v2_research import write_json
from user_data.strategy_lib.funding_extreme_contrarian import (
    HOLD_MINUTES,
    LOOKBACK_SETTLEMENTS,
    LOWER_QUANTILE,
    MINIMUM_STOP_FRACTION,
    STOP_ATR,
    TARGET_R,
    UPPER_QUANTILE,
    generate_events,
)
from user_data.strategy_lib.v2_backtester import EntryEvent

HOLD = pd.Timedelta(HOLD_MINUTES * 60_000_000_000, unit="ns")
PROTOCOL = ROOT / "docs/research/JEV_FUNDING_EXTREME_CONTRARIAN_PROTOCOL.md"


def _complete(events: list[EntryEvent], start, end) -> list[EntryEvent]:
    return [event for event in events if start <= event.date and event.date + HOLD <= end]


def _all_events(frames, funding) -> list[EntryEvent]:
    events = [generate_events(pair, frames[pair], funding[pair]) for pair in frames]
    return sorted([event for batch in events for event in batch],
                  key=lambda event: (event.date, event.pair, event.side))


def _reserve_passed(metrics, portfolio) -> bool:
    regular = metrics["regular"]
    return (
        regular["n"] >= 100 and regular["total_net_return"] > 0
        and regular["pf"] is not None and regular["pf"] >= 1.2
        and portfolio["regular"]["trades"] >= 100
        and portfolio["regular"]["final_equity"] > 100
        and portfolio["regular"]["drawdown"] <= .20
        and all(metrics[key]["total_net_return"] >= 0
                and ((metrics[key]["pf"] is not None and metrics[key]["pf"] >= 1.0)
                     or metrics[key]["pf_unbounded"])
                for key in ("cost_stress", "strict_stress"))
        and all(portfolio[key]["final_equity"] >= 100
                and portfolio[key]["pf"] is not None and portfolio[key]["pf"] >= 1.0
                for key in ("cost_stress", "strict_stress"))
    )


def run(args: argparse.Namespace) -> None:
    out: Path = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "scripts/run_cross_sectional_momentum_research.py",
        ROOT / "scripts/run_jev_profit_research.py",
        ROOT / "user_data/strategy_lib/funding_extreme_contrarian.py",
        ROOT / "user_data/strategy_lib/scalp_paths.py",
        ROOT / "user_data/strategy_lib/v2_signal_engine.py",
        ROOT / "user_data/strategy_lib/v2_backtester.py",
    ]
    manifest: dict[str, object] = {
        "status": "training",
        "candidate": "contrarian entries after extreme settled funding rates",
        "lookback_settlements": LOOKBACK_SETTLEMENTS,
        "lower_quantile": LOWER_QUANTILE,
        "upper_quantile": UPPER_QUANTILE,
        "stop_atr": STOP_ATR,
        "minimum_stop_fraction": MINIMUM_STOP_FRACTION,
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
    events = _all_events(frames, funding)
    training_events = _complete(events, TRAIN_START, SPLIT_A)
    manifest["base_training_events"] = len(training_events)
    metrics = _label_scenarios(
        frames, funding, training_events, TRAIN_START, SPLIT_A, out,
        "training", hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "training-metrics.json", metrics)
    if not _qualifies_labels(metrics, 80):
        manifest.update(
            status="no_qualified_training_candidate",
            reason="Training failed frozen sample, net-win, PF, mean-return or cost/delay gates; development and reserve were not scored",
        )
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    development_events = _complete(events, SPLIT_A, END)
    a_events = _complete(development_events, SPLIT_A, SPLIT_B)
    manifest["status"] = "development_a"
    write_json(out / "manifest.json", manifest)
    a_metrics = _label_scenarios(
        frames, funding, a_events, SPLIT_A, SPLIT_B, out,
        "development-a", hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-a-metrics.json", a_metrics)
    if not _qualifies_labels(a_metrics, 30):
        manifest.update(status="development_a_failed",
                        reason="Development A failed frozen gates; development B and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    b_events = _complete(development_events, SPLIT_B, END)
    manifest["status"] = "development_b"
    write_json(out / "manifest.json", manifest)
    b_metrics = _label_scenarios(
        frames, funding, b_events, SPLIT_B, END, out,
        "development-b", hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-b-metrics.json", b_metrics)
    if not (_qualifies_labels(b_metrics, 30)
            and a_metrics["regular"]["n"] + b_metrics["regular"]["n"] >= 100):
        manifest.update(status="development_b_failed",
                        reason="Development B or combined sample failed; portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    portfolio_a = _portfolio_scenarios(
        frames, funding, a_events, SPLIT_A, SPLIT_B,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-a-portfolio.json", portfolio_a)
    if not _qualifies_portfolio(portfolio_a):
        manifest.update(status="development_a_portfolio_failed",
                        reason="Development A portfolio failed; reserve was not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    portfolio_b = _portfolio_scenarios(
        frames, funding, b_events, SPLIT_B, END,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "development-b-portfolio.json", portfolio_b)
    if not (_qualifies_portfolio(portfolio_b)
            and portfolio_a["regular"]["trades"] + portfolio_b["regular"]["trades"] >= 100):
        manifest.update(status="development_b_portfolio_failed",
                        reason="Development B portfolio or combined sample failed; reserve was not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest.update(status="reserve_validation", heldout_prices_opened=True)
    write_json(out / "manifest.json", manifest)
    audit_frames, audit_funding, audit_hashes = load_dataset(
        args.holdout_dir, AUDIT_SYMBOLS, AUDIT_START,
    )
    manifest["audit_data_sha256"] = audit_hashes
    write_json(out / "manifest.json", manifest)
    audit_events = _complete(_all_events(audit_frames, audit_funding), AUDIT_START, END)
    audit_metrics = _label_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END, out,
        "reserve", hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "reserve-metrics.json", audit_metrics)
    audit_portfolio = _portfolio_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END,
        hold_minutes=HOLD_MINUTES, target_r=TARGET_R,
    )
    write_json(out / "reserve-portfolio.json", audit_portfolio)
    passed = _reserve_passed(audit_metrics, audit_portfolio)
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
                        default=ROOT / "user_data/backtest_results/funding-extreme-contrarian-20260927-train-only")
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
