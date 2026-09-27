#!/usr/bin/env python3
"""Run one frozen range liquidity-sweep trial; never sends live orders."""
from __future__ import annotations

import argparse
from dataclasses import asdict
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
from user_data.strategy_lib.liquidity_sweep import (
    LiquiditySweepParameters,
    generate_events,
)

TARGET_R = 1.5
DEFAULT_HOLD_MINUTES = 60
PARAMS = LiquiditySweepParameters()


def _all_events(frames, params=PARAMS):
    events = [event for pair, frame in frames.items()
              for event in generate_events(pair, frame, params)]
    return sorted(events, key=lambda event: (event.date, event.pair, event.side))


def _parameters(lookback_bars: int, hold_minutes: int) -> LiquiditySweepParameters:
    if hold_minutes == 240 and lookback_bars != 144:
        raise ValueError("240-minute hold variant requires the frozen 144-bar range")
    return LiquiditySweepParameters(
        lookback_bars=lookback_bars,
        cooldown_bars=49 if hold_minutes == 240 else 13,
    )


def run(args: argparse.Namespace) -> None:
    out: Path = args.output_dir
    params = _parameters(args.lookback_bars, args.hold_minutes)
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    protocol_name = (
        "JEV_12H_SWEEP_HOLD_240_PROTOCOL.md" if args.hold_minutes == 240
        else "JEV_12H_LIQUIDITY_SWEEP_PROTOCOL.md" if args.lookback_bars == 144
        else "JEV_RANGE_LIQUIDITY_SWEEP_PROTOCOL.md"
    )
    protocol = ROOT / "docs/research" / protocol_name
    candidate = (
        "12-hour range boundary sweep and reclaim, 4-hour max hold"
        if args.hold_minutes == 240 else
        "12-hour range boundary sweep and reclaim"
        if args.lookback_bars == 144 else
        "2-hour range boundary sweep and reclaim"
    )
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "scripts/run_cross_sectional_momentum_research.py",
        ROOT / "scripts/run_jev_profit_research.py",
        ROOT / "user_data/strategy_lib/liquidity_sweep.py",
        ROOT / "user_data/strategy_lib/scalp_paths.py",
        ROOT / "user_data/strategy_lib/v2_signal_engine.py",
        ROOT / "user_data/strategy_lib/v2_backtester.py",
    ]
    manifest: dict[str, object] = {
        "status": "training",
        "candidate": candidate,
        "parameters": asdict(params),
        "target_r": TARGET_R,
        "hold_minutes": args.hold_minutes,
        "development_symbols": DEV_SYMBOLS,
        "audit_symbols": AUDIT_SYMBOLS,
        "train": [TRAIN_START.isoformat(), SPLIT_A.isoformat()],
        "development_a": [SPLIT_A.isoformat(), SPLIT_B.isoformat()],
        "development_b": [SPLIT_B.isoformat(), END.isoformat()],
        "heldout_prices_opened": False,
        "live_claim_allowed": False,
        "protocol_sha256": fingerprint(protocol),
        "code_sha256": {str(path.relative_to(ROOT)): fingerprint(path) for path in code_paths},
    }
    write_json(out / "manifest.json", manifest)
    frames, funding, hashes = load_dataset(args.data_dir, DEV_SYMBOLS, TRAIN_START)
    manifest["development_data_sha256"] = hashes
    write_json(out / "manifest.json", manifest)
    events = _all_events(frames, params)
    _events_csv(events, out / "candidate-events.csv")

    train = _label_scenarios(
        frames, funding, events, TRAIN_START, SPLIT_A, out, "training",
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
    )
    write_json(out / "training-metrics.json", train)
    if not _qualifies_labels(train, 80):
        manifest.update(status="no_qualified_training_candidate",
                        reason="Training labels failed frozen sample, net-win, PF, return or cost-stress gates; development and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_a"
    write_json(out / "manifest.json", manifest)
    a = _label_scenarios(
        frames, funding, events, SPLIT_A, SPLIT_B, out, "development-a",
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
    )
    write_json(out / "development-a-label-metrics.json", a)
    if not _qualifies_labels(a, 30):
        manifest.update(status="development_a_failed",
                        reason="Development A failed frozen gates; development B and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_b"
    write_json(out / "manifest.json", manifest)
    b = _label_scenarios(
        frames, funding, events, SPLIT_B, END, out, "development-b",
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
    )
    write_json(out / "development-b-label-metrics.json", b)
    if not (_qualifies_labels(b, 30)
            and a["regular"]["n"] + b["regular"]["n"] >= 100):
        manifest.update(status="development_b_failed",
                        reason="Development B or combined sample failed; portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_a"
    write_json(out / "manifest.json", manifest)
    portfolio_a = _portfolio_scenarios(
        frames, funding, events, SPLIT_A, SPLIT_B,
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
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
        frames, funding, events, SPLIT_B, END,
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
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
    audit_frames, audit_funding, audit_hashes = load_dataset(
        args.holdout_dir, AUDIT_SYMBOLS, AUDIT_START,
    )
    manifest["heldout_prices_opened"] = True
    manifest["audit_data_sha256"] = audit_hashes
    write_json(out / "manifest.json", manifest)
    audit_events = _all_events(audit_frames, params)
    audit = _label_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END, out, "reserve",
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
    )
    write_json(out / "reserve-label-metrics.json", audit)
    portfolio_audit = _portfolio_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END,
        hold_minutes=args.hold_minutes, target_r=TARGET_R,
    )
    write_json(out / "reserve-portfolio-metrics.json", portfolio_audit)
    regular = audit["regular"]
    passed = (
        regular["n"] >= 100 and regular["total_net_return"] > 0
        and regular["pf"] is not None and regular["pf"] >= 1.2
        and portfolio_audit["regular"]["trades"] >= 100
        and portfolio_audit["regular"]["final_equity"] > 100
        and portfolio_audit["regular"]["drawdown"] <= .20
        and all(audit[key]["total_net_return"] >= 0
                and ((audit[key]["pf"] is not None and audit[key]["pf"] >= 1.0)
                     or audit[key]["pf_unbounded"])
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
    parser.add_argument("--lookback-bars", type=int, choices=(24, 144), default=24,
                        help="Range length in completed 5-minute bars: 24 (2h) or 144 (12h)")
    parser.add_argument("--hold-minutes", type=int, choices=(60, 240),
                        default=DEFAULT_HOLD_MINUTES,
                        help="Maximum holding time: 60 or 240 minutes; 240 requires 144 lookback bars")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.output_dir is None:
        suffix = "2h"
        if args.lookback_bars == 144:
            suffix = "12h-hold240m" if args.hold_minutes == 240 else "12h"
        args.output_dir = ROOT / "user_data/backtest_results" / (
            f"liquidity-sweep-{suffix}-20260927-train-only"
        )
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
