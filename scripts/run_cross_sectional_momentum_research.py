#!/usr/bin/env python3
"""Run one frozen cross-sectional momentum trial; never sends live orders."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.run_jev_ma_research import fingerprint
from scripts.run_jev_profit_research import (
    AUDIT_START,
    AUDIT_SYMBOLS,
    DEV_SYMBOLS,
    load_dataset,
)
from scripts.run_okx_v2_research import write_json
from user_data.strategy_lib.cross_sectional_momentum import (
    CrossSectionalMomentumParameters,
    generate_events,
)
from user_data.strategy_lib.scalp_paths import evaluate_events
from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)

TRAIN_START = pd.Timestamp("2026-03-01", tz="UTC")
SPLIT_A = pd.Timestamp("2026-06-01", tz="UTC")
SPLIT_B = pd.Timestamp("2026-07-15", tz="UTC")
END = pd.Timestamp("2026-09-26", tz="UTC")
PROTOCOL = ROOT / "docs/research/JEV_CROSS_SECTIONAL_MOMENTUM_PROTOCOL.md"
PARAMS = CrossSectionalMomentumParameters()
TARGET_R = 1.0
HOLD_MINUTES = 30
DELAY = pd.Timedelta(minutes=5)


def _period(events: list[EntryEvent], start: pd.Timestamp, end: pd.Timestamp,
            *, delay: pd.Timedelta = pd.Timedelta(0)) -> list[EntryEvent]:
    """Keep signals and their complete exit window within the requested split."""
    return [event for event in events
            if start <= event.date and event.date + delay + pd.Timedelta(minutes=HOLD_MINUTES) <= end]


def _delay(events: list[EntryEvent]) -> list[EntryEvent]:
    return [replace(event, date=event.date + DELAY) for event in events]


def _metrics(rows: pd.DataFrame) -> dict[str, object]:
    if rows.empty:
        return {"n": 0, "win_rate": None, "pf": None,
                "pf_unbounded": False, "mean_net_return": None,
                "total_net_return": 0.0}
    returns = rows.net_return.astype(float)
    gross_win = float(returns[returns > 0].sum())
    gross_loss = float(-returns[returns < 0].sum())
    pf = gross_win / gross_loss if gross_loss else (None if gross_win else 0.0)
    return {
        "n": int(len(rows)),
        "win_rate": float((returns > 0).mean()),
        "pf": pf,
        "pf_unbounded": bool(gross_win > 0 and gross_loss == 0),
        "mean_net_return": float(returns.mean()),
        "total_net_return": float(returns.sum()),
    }


def _label(frames, funding, events, *, fee: float, slippage: float, missing_funding: float,
           out: Path | None = None, name: str = "") -> dict[str, object]:
    rows = evaluate_events(
        frames, events, TARGET_R, HOLD_MINUTES,
        fee_rate=fee,
        slippage_rate=slippage,
        funding_rate_per_8h=missing_funding,
        funding_frames=funding,
    )
    if out is not None:
        rows.to_csv(out / f"{name}-labels.csv", index=False)
    return _metrics(rows)


def _label_scenarios(frames, funding, events, start, end, out, prefix):
    regular_events = _period(events, start, end)
    stress_events = regular_events
    strict_events = _delay(_period(events, start, end, delay=DELAY))
    return {
        "regular": _label(frames, funding, regular_events, fee=.0005, slippage=.0005,
                          missing_funding=.0001, out=out, name=f"{prefix}-regular"),
        "cost_stress": _label(frames, funding, stress_events, fee=.001, slippage=.001,
                              missing_funding=.0002, out=out, name=f"{prefix}-cost-stress"),
        "strict_stress": _label(frames, funding, strict_events, fee=.001, slippage=.001,
                                missing_funding=.0002, out=out, name=f"{prefix}-strict-stress"),
        "delayed_regular": _label(frames, funding, strict_events, fee=.0005, slippage=.0005,
                                  missing_funding=.0001, out=out, name=f"{prefix}-delayed-regular"),
    }


def _qualifies_labels(metrics: dict[str, object], minimum: int) -> bool:
    regular = metrics["regular"]
    if not (regular["n"] >= minimum and regular["win_rate"] is not None
            and regular["win_rate"] >= .5 and regular["mean_net_return"] > 0
            and ((regular["pf"] is not None and regular["pf"] >= 1.2)
                 or regular["pf_unbounded"])):
        return False
    for key in ("cost_stress", "strict_stress"):
        stress = metrics[key]
        if not (stress["n"] >= minimum and stress["mean_net_return"] is not None
                and stress["mean_net_return"] >= 0
                and ((stress["pf"] is not None and stress["pf"] >= 1.0)
                     or stress["pf_unbounded"])):
            return False
    return True


def _portfolio(frames, funding, events, start, end, *, fee, slippage,
               missing_funding, delayed=False):
    selected = _period(events, start, end, delay=DELAY if delayed else pd.Timedelta(0))
    if delayed:
        selected = _delay(selected)
    options = BacktestOptions(
        initial_equity=100.0,
        leverage=3.0,
        exit_mode="fixed3",
        fee_rate=fee,
        slippage_rate=slippage,
        normal_trade_risk=.0075,
        max_portfolio_risk=.02,
        missing_funding_rate_per_8h=missing_funding,
        take_profit_r=TARGET_R,
        max_hold_minutes=HOLD_MINUTES,
        candle_minutes=5,
    )
    result = simulate_portfolio(
        frames, selected, options, start=start, end=end, funding_frames=funding,
    )
    summary = summarize_backtest(result)
    if not result.trades:
        summary.update(win_rate=None, pf=None)
    return summary


def _qualifies_portfolio(metrics: dict[str, object], minimum: int = 30) -> bool:
    regular = metrics["regular"]
    if not (regular["trades"] >= minimum and regular["final_equity"] > 100
            and regular["pf"] is not None and regular["pf"] >= 1.2
            and regular["drawdown"] <= .20):
        return False
    for key in ("cost_stress", "strict_stress"):
        stress = metrics[key]
        if not (stress["final_equity"] >= 100 and stress["pf"] is not None
                and stress["pf"] >= 1.0):
            return False
    return True


def _portfolio_scenarios(frames, funding, events, start, end):
    return {
        "regular": _portfolio(frames, funding, events, start, end, fee=.0005,
                              slippage=.0005, missing_funding=.0001),
        "cost_stress": _portfolio(frames, funding, events, start, end, fee=.001,
                                  slippage=.001, missing_funding=.0002),
        "strict_stress": _portfolio(frames, funding, events, start, end, fee=.001,
                                     slippage=.001, missing_funding=.0002, delayed=True),
        "delayed_regular": _portfolio(frames, funding, events, start, end, fee=.0005,
                                      slippage=.0005, missing_funding=.0001, delayed=True),
    }


def _events_csv(events: list[EntryEvent], path: Path) -> None:
    pd.DataFrame([{
        "date": event.date, "pair": event.pair, "side": event.side,
        "stop_price": event.stop_price, "rank": event.rank,
    } for event in events]).to_csv(path, index=False)


def run(args: argparse.Namespace) -> None:
    out: Path = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "user_data/strategy_lib/cross_sectional_momentum.py",
        ROOT / "user_data/strategy_lib/scalp_paths.py",
        ROOT / "user_data/strategy_lib/v2_backtester.py",
    ]
    manifest: dict[str, object] = {
        "status": "training",
        "candidate": "one-hour cross-sectional momentum, 30-minute rebalance and hold",
        "parameters": asdict(PARAMS),
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
    events = generate_events(frames, PARAMS)
    _events_csv(events, out / "candidate-events.csv")
    train_metrics = _label_scenarios(frames, funding, events, TRAIN_START, SPLIT_A, out, "training")
    write_json(out / "training-metrics.json", train_metrics)
    if not _qualifies_labels(train_metrics, 80):
        manifest.update(status="no_qualified_training_candidate",
                        reason="Training labels failed sample, net win-rate, PF, net return or stress gates; development and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_a"
    write_json(out / "manifest.json", manifest)
    a_metrics = _label_scenarios(frames, funding, events, SPLIT_A, SPLIT_B, out, "development-a")
    write_json(out / "development-a-label-metrics.json", a_metrics)
    if not _qualifies_labels(a_metrics, 30):
        manifest.update(status="development_a_failed",
                        reason="Development A failed frozen label gates; development B and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "development_b"
    write_json(out / "manifest.json", manifest)
    b_metrics = _label_scenarios(frames, funding, events, SPLIT_B, END, out, "development-b")
    write_json(out / "development-b-label-metrics.json", b_metrics)
    if not (_qualifies_labels(b_metrics, 30)
            and a_metrics["regular"]["n"] + b_metrics["regular"]["n"] >= 100):
        manifest.update(status="development_b_failed",
                        reason="Development B or combined sample failed frozen label gates; portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_a"
    write_json(out / "manifest.json", manifest)
    portfolio_a = _portfolio_scenarios(frames, funding, events, SPLIT_A, SPLIT_B)
    write_json(out / "development-a-portfolio-metrics.json", portfolio_a)
    if not _qualifies_portfolio(portfolio_a):
        manifest.update(status="development_a_portfolio_failed",
                        reason="Development A portfolio risk or return gates failed; development B portfolio and reserve were not scored")
        write_json(out / "manifest.json", manifest)
        print(manifest["status"], flush=True)
        return

    manifest["status"] = "portfolio_b"
    write_json(out / "manifest.json", manifest)
    portfolio_b = _portfolio_scenarios(frames, funding, events, SPLIT_B, END)
    write_json(out / "development-b-portfolio-metrics.json", portfolio_b)
    if not (_qualifies_portfolio(portfolio_b)
            and portfolio_a["regular"]["trades"] + portfolio_b["regular"]["trades"] >= 100):
        manifest.update(status="development_b_portfolio_failed",
                        reason="Development B portfolio or combined sample gates failed; reserve was not scored")
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
    audit_events = generate_events(audit_frames, PARAMS)
    audit_metrics = _label_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END, out, "reserve",
    )
    write_json(out / "reserve-label-metrics.json", audit_metrics)
    audit_portfolio = _portfolio_scenarios(
        audit_frames, audit_funding, audit_events, AUDIT_START, END,
    )
    write_json(out / "reserve-portfolio-metrics.json", audit_portfolio)
    reserve_regular = audit_metrics["regular"]
    reserve_pass = (
        reserve_regular["n"] >= 100 and reserve_regular["total_net_return"] > 0
        and reserve_regular["pf"] is not None and reserve_regular["pf"] >= 1.2
        and audit_portfolio["regular"]["trades"] >= 100
        and audit_portfolio["regular"]["final_equity"] > 100
        and audit_portfolio["regular"]["drawdown"] <= .20
        and all(audit_metrics[key]["total_net_return"] >= 0
                and ((audit_metrics[key]["pf"] is not None and audit_metrics[key]["pf"] >= 1.0)
                     or audit_metrics[key]["pf_unbounded"])
                for key in ("cost_stress", "strict_stress"))
        and all(audit_portfolio[key]["final_equity"] >= 100
                and audit_portfolio[key]["pf"] is not None and audit_portfolio[key]["pf"] >= 1.0
                for key in ("cost_stress", "strict_stress"))
    )
    manifest.update(
        status=("historic_validation_passed_requires_forward_simulation"
                if reserve_pass else "reserve_validation_failed"),
        reserve_passed=bool(reserve_pass),
        reason=(None if reserve_pass else "Frozen reserve gates failed; do not retune on these results"),
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
                        default=ROOT / "user_data/backtest_results/cross-sectional-momentum-20260927")
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
