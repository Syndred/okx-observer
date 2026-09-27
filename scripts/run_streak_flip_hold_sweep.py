#!/usr/bin/env python3
"""Screen frozen streak-flip holding limits, then gate each later stage."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    DEV_SYMBOLS,
    load_dataset,
)
from scripts.run_okx_v2_research import write_json
from user_data.strategy_lib.streak_flip_engine import (
    StreakFlipParameters,
    generate_events,
)

PARAMS = StreakFlipParameters()
HOLD_CANDIDATES = (5, 10, 15, 30)
PROTOCOL = ROOT / "docs/research/JEV_STREAK_FLIP_HOLD_SWEEP_PROTOCOL.md"


def _all_events(frames):
    events = [event for pair, frame in frames.items()
              for event in generate_events(pair, frame, PARAMS)]
    return sorted(events, key=lambda event: (event.date, event.pair, event.side))


def _pf_value(metrics):
    if metrics["pf_unbounded"]:
        return math.inf
    return float(metrics["pf"]) if metrics["pf"] is not None else -math.inf


def _select_candidate(trials):
    qualified = [(hold, metrics) for hold, metrics in trials.items()
                 if _qualifies_labels(metrics, 80)]
    if not qualified:
        return None
    return max(qualified, key=lambda item: (
        min(_pf_value(item[1]["cost_stress"]), _pf_value(item[1]["strict_stress"])),
        _pf_value(item[1]["regular"]),
        -item[0],
    ))


def run(args: argparse.Namespace) -> None:
    out: Path = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "scripts/run_cross_sectional_momentum_research.py",
        ROOT / "user_data/strategy_lib/streak_flip_engine.py",
        ROOT / "user_data/strategy_lib/scalp_paths.py",
        ROOT / "user_data/strategy_lib/v2_backtester.py",
    ]
    manifest: dict[str, object] = {
        "status": "training",
        "candidate": "confirmed four-candle streak flip; fixed 1R target",
        "parameters": asdict(PARAMS),
        "hold_candidates_minutes": HOLD_CANDIDATES,
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
    events = _all_events(frames)
    _events_csv(events, out / "candidate-events.csv")

    trials = {}
    for hold in HOLD_CANDIDATES:
        metrics = _label_scenarios(
            frames, funding, events, TRAIN_START, SPLIT_A, out,
            f"training-{hold}m", hold_minutes=hold,
        )
        trials[str(hold)] = metrics
        write_json(out / "training-hold-sweep.json", {"trials": trials})
    selected = _select_candidate({int(key): value for key, value in trials.items()})
    if selected is None:
        manifest.update(
            status="no_qualified_training_candidate",
            reason="All frozen holding limits failed sample, net-win, PF, net-return or stress gates; no development or reserve performance was scored",
            training_hold_sweep=trials,
        )
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    selected_hold, selected_metrics = selected
    frozen = {
        "candidate": manifest["candidate"],
        "parameters": asdict(PARAMS),
        "target_r": 1.0,
        "hold_minutes": selected_hold,
        "training": selected_metrics,
        "selection_rule": "largest minimum cost/strict-stress PF, then regular PF, then shortest holding time",
    }
    write_json(out / "frozen-candidate.json", frozen)
    manifest.update(status="development_a", selected_hold_minutes=selected_hold,
                    training_hold_sweep=trials)
    write_json(out / "manifest.json", manifest)

    a = _label_scenarios(frames, funding, events, SPLIT_A, SPLIT_B, out,
                         "development-a", hold_minutes=selected_hold)
    write_json(out / "development-a-label-metrics.json", a)
    if not _qualifies_labels(a, 30):
        manifest.update(status="development_a_failed",
                        reason="Development A failed fixed gates; development B and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_b"
    write_json(out / "manifest.json", manifest)
    b = _label_scenarios(frames, funding, events, SPLIT_B, END, out,
                         "development-b", hold_minutes=selected_hold)
    write_json(out / "development-b-label-metrics.json", b)
    if not (_qualifies_labels(b, 30) and a["regular"]["n"] + b["regular"]["n"] >= 100):
        manifest.update(status="development_b_failed",
                        reason="Development B or combined label sample failed; portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_a"
    write_json(out / "manifest.json", manifest)
    portfolio_a = _portfolio_scenarios(frames, funding, events, SPLIT_A, SPLIT_B,
                                        hold_minutes=selected_hold)
    write_json(out / "development-a-portfolio-metrics.json", portfolio_a)
    if not _qualifies_portfolio(portfolio_a):
        manifest.update(status="development_a_portfolio_failed",
                        reason="Development A portfolio failed; development B portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_b"
    write_json(out / "manifest.json", manifest)
    portfolio_b = _portfolio_scenarios(frames, funding, events, SPLIT_B, END,
                                        hold_minutes=selected_hold)
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
    audit_frames, audit_funding, audit_hashes = load_dataset(
        args.holdout_dir, AUDIT_SYMBOLS, AUDIT_START,
    )
    manifest["heldout_prices_opened"] = True
    manifest["audit_data_sha256"] = audit_hashes
    write_json(out / "manifest.json", manifest)
    audit_events = _all_events(audit_frames)
    audit = _label_scenarios(audit_frames, audit_funding, audit_events,
                             AUDIT_START, END, out, "reserve",
                             hold_minutes=selected_hold)
    write_json(out / "reserve-label-metrics.json", audit)
    portfolio_audit = _portfolio_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END,
        hold_minutes=selected_hold,
    )
    write_json(out / "reserve-portfolio-metrics.json", portfolio_audit)
    regular = audit["regular"]
    passed = (
        regular["n"] >= 100 and regular["total_net_return"] > 0
        and _pf_value(regular) >= 1.2
        and portfolio_audit["regular"]["trades"] >= 100
        and portfolio_audit["regular"]["final_equity"] > 100
        and portfolio_audit["regular"]["drawdown"] <= .20
        and all(audit[key]["total_net_return"] >= 0 and _pf_value(audit[key]) >= 1.0
                for key in ("cost_stress", "strict_stress"))
        and all(portfolio_audit[key]["final_equity"] >= 100
                and portfolio_audit[key]["pf"] is not None
                and portfolio_audit[key]["pf"] >= 1.0
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
                        default=ROOT / "user_data/backtest_results/streak-flip-hold-sweep-20260927")
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
