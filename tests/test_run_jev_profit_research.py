"""Profit-study acceptance and isolation checks using synthetic data only."""

from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts import run_jev_profit_research as research
from user_data.strategy_lib.v2_backtester import EntryEvent


@pytest.mark.parametrize("update,expected", [
    ({}, True), ({"n": 29}, False), ({"pf": 1.199}, False),
    ({"pf": None}, False), ({"mean_net_return": 0}, False),
    ({"mean_net_return": -.001}, False),
])
def test_independent_gate_requires_sample_pf_and_positive_net(update, expected):
    result = {"n": 30, "pf": 1.2, "mean_net_return": .001, **update}
    assert research.independent_pass(result) is expected


def test_independent_gate_uses_requested_sample_minimum():
    result = {"n": 79, "pf": 2, "mean_net_return": .01}
    assert research.independent_pass(result)
    assert not research.independent_pass(result, 80)
    result["n"] = 80
    assert research.independent_pass(result, 80)


@pytest.mark.parametrize("regular_update,stress_update,expected", [
    ({}, {}, True), ({"trades": 29}, {}, False), ({"pf": 1.199}, {}, False),
    ({"pf": None}, {}, False), ({"final_equity": 100}, {}, False),
    ({"drawdown": .20001}, {}, False), ({}, {"trades": 0, "pf": None}, False),
    ({}, {"pf": .999}, False), ({}, {"final_equity": 99.99}, False),
])
def test_portfolio_gate_requires_profit_drawdown_and_stress(regular_update, stress_update, expected):
    regular = {"trades": 30, "pf": 1.2, "final_equity": 100.01, "drawdown": .2, **regular_update}
    stress = {"trades": 1, "pf": 1., "final_equity": 100., **stress_update}
    assert research.portfolio_pass(regular, stress) is expected


def test_portfolio_audit_gate_requires_100_trades():
    regular = {"trades": 99, "pf": 1.2, "final_equity": 110, "drawdown": .1}
    stress = {"trades": 99, "pf": 1.1, "final_equity": 101}
    assert research.portfolio_pass(regular, stress)
    assert not research.portfolio_pass(regular, stress, 100)
    regular["trades"] = 100
    assert research.portfolio_pass(regular, stress, 100)


def model_inputs():
    dates = pd.date_range("2026-08-01", periods=31, freq="5min", tz="UTC")
    signals = pd.DataFrame({
        "date": dates - pd.Timedelta(minutes=5), "decision_at": dates,
        "open": 100., "high": 101., "low": 99., "close": 100., "volume": 20.,
        "fast": 100.1, "slow": 99.9, "atr14": 1., "bias15": .2, "bias60": .3,
        "compression": .2, "instrument": "SECRET-USDT-SWAP",
    })
    event = EntryEvent(dates[25], "SECRET-USDT-SWAP", "long", 98, 1)
    funding = pd.DataFrame({"date": [dates[0], dates[25], dates[26]], "rate": [.0001, -.0002, .8]})
    return signals, event, funding


def test_model_state_excludes_future_signals_and_funding_and_limits_history():
    signals, event, funding = model_inputs()
    params = research.ProfitParameters()
    initial = research.model_state(event, signals, params, 2, 30, funding)
    changed = signals.copy()
    future = changed.decision_at > event.date
    changed.loc[future, ["open", "high", "low", "close", "volume", "fast", "slow", "atr14", "bias15", "bias60", "compression"]] = 999999
    changed_funding = funding.copy()
    changed_funding.loc[changed_funding.date > event.date, "rate"] = -999999
    assert research.model_state(event, changed, params, 2, 30, changed_funding) == initial
    assert len(initial["bars_5m_oldest_first"]) == 24
    assert initial["last_known_funding_rate"] == -.0002
    assert initial["bars_5m_oldest_first"][-1]["close"] == 0
    # Bars use price/ref - 1; indicator features use indicator/ref.
    assert initial["features"]["fast"] == pytest.approx(1.001)
    assert initial["features"]["slow"] == pytest.approx(.999)
    assert initial["execution_rules"]["fixed_stop_price_divided_by_last_closed_price"] == .98


def test_model_state_has_no_instrument_or_calendar_identifiers():
    signals, event, funding = model_inputs()
    params = research.ProfitParameters()
    initial = research.model_state(event, signals, params, 2, 30, funding)
    shifted = signals.copy()
    shifted[["date", "decision_at"]] += pd.Timedelta(days=100)
    shifted["instrument"] = "OTHER-USDT-SWAP"
    shifted_funding = funding.copy()
    shifted_funding["date"] += pd.Timedelta(days=100)
    shifted_event = replace(event, date=event.date + pd.Timedelta(days=100), pair="OTHER-USDT-SWAP")
    assert research.model_state(shifted_event, shifted, params, 2, 30, shifted_funding) == initial
    encoded = json.dumps(initial)
    assert "SECRET" not in encoded and "2026-08-01" not in encoded


def test_model_state_requires_exact_causal_decision_and_no_future_funding_fallback():
    signals, event, funding = model_inputs()
    params = research.ProfitParameters()
    with pytest.raises(ValueError, match="missing causal decision"):
        research.model_state(event, signals.loc[signals.decision_at != event.date], params, 2, 30, funding)
    state = research.model_state(event, signals, params, 2, 30, funding.loc[funding.date > event.date])
    assert state["last_known_funding_rate"] is None


@pytest.mark.parametrize("stress", [False, True])
def test_label_and_portfolio_pass_actual_funding_unchanged(monkeypatch, stress):
    signals, event, frame = model_inputs()
    frames, funding, events = {event.pair: signals}, {event.pair: frame}, [event]
    before = deepcopy(funding)
    evaluate = Mock(return_value="labels")
    simulate = Mock(return_value="portfolio")
    monkeypatch.setattr(research, "evaluate_events", evaluate)
    monkeypatch.setattr(research, "simulate_portfolio", simulate)
    assert research.label(frames, funding, events, 2, 30, stress) == "labels"
    assert research.portfolio(frames, funding, events, 2, 30, research.START, research.END, stress) == "portfolio"
    label_args, label_kwargs = evaluate.call_args
    port_args, port_kwargs = simulate.call_args
    assert label_args == (frames, events, 2, 30)
    assert port_args[0] is frames and port_args[1] is events
    assert label_kwargs["funding_frames"] is funding and port_kwargs["funding_frames"] is funding
    options = port_args[2]
    assert label_kwargs["fee_rate"] == options.fee_rate == (.001 if stress else .0005)
    assert label_kwargs["slippage_rate"] == options.slippage_rate == (.001 if stress else .0005)
    assert label_kwargs["funding_rate_per_8h"] == options.missing_funding_rate_per_8h == (.0002 if stress else .0001)
    assert port_kwargs["start"] == research.START and port_kwargs["end"] == research.END
    assert options.take_profit_r == 2 and options.max_hold_minutes == 30 and options.candle_minutes == 5
    pd.testing.assert_frame_equal(funding[event.pair], before[event.pair])


def test_stress_changes_only_execution_and_missing_funding_costs(monkeypatch):
    evaluate, simulate = Mock(), Mock()
    monkeypatch.setattr(research, "evaluate_events", evaluate)
    monkeypatch.setattr(research, "simulate_portfolio", simulate)
    frames, funding, events = {}, {}, []
    for stress in [False, True]:
        research.label(frames, funding, events, 2, 30, stress)
        research.portfolio(frames, funding, events, 2, 30, research.START, research.END, stress)
    regular, stress = [call.kwargs for call in evaluate.call_args_list]
    assert {key for key in regular if regular[key] != stress[key]} == {
        "fee_rate", "slippage_rate", "funding_rate_per_8h",
    }
    regular, stress = [asdict(call.args[2]) for call in simulate.call_args_list]
    changed = {key for key in regular if regular[key] != stress[key]}
    assert changed == {"fee_rate", "slippage_rate", "missing_funding_rate_per_8h"}
    assert all(stress[key] == 2 * regular[key] for key in changed)


def test_failed_development_never_loads_reserved_prices(monkeypatch, tmp_path):
    """Run the full decision flow with empty synthetic trades and no API or data IO."""
    dataset = Mock(return_value=({"SYNTHETIC": pd.DataFrame()}, {}, {}))
    monkeypatch.setattr(research, "load_dataset", dataset)
    monkeypatch.setattr(research, "fingerprint", lambda _: "synthetic-hash")
    monkeypatch.setattr(research, "scan_profit", lambda *_: pd.DataFrame())
    monkeypatch.setattr(research, "event_set", lambda *_: [])
    monkeypatch.setattr(research, "label", lambda *_: pd.DataFrame(columns=["pair", "date", "side"]))
    monkeypatch.setattr(research, "stats", lambda _: {"n": 0, "pf": None, "mean_net_return": 0})
    monkeypatch.setattr(research, "measure_portfolio", lambda *_: {
        "regular": {"trades": 0, "pf": None, "final_equity": 100, "drawdown": 0},
        "stress": {"trades": 0, "pf": None, "final_equity": 100, "drawdown": 0},
        "passed": False,
    })
    monkeypatch.setattr(research, "load_key_file", Mock())
    monkeypatch.setattr(research, "JevClient", Mock())
    predictions = Mock(return_value={})
    monkeypatch.setattr(research, "score_predictions", predictions)
    args = SimpleNamespace(output_dir=tmp_path / "output", data_dir=tmp_path / "dev",
                           audit_dir=tmp_path / "reserved", env_file=None)
    research.run(args)
    dataset.assert_called_once_with(args.data_dir, research.DEV_SYMBOLS, research.START)
    manifest = json.loads((args.output_dir / "manifest.json").read_text())
    assert manifest["status"] == "no_profitable_development_candidate"
    assert manifest["heldout_prices_opened"] is False
    assert manifest["live_claim_allowed"] is False
    assert all(call.args[-1] != "audit" for call in predictions.call_args_list)


def test_diagnostic_cutoff_is_real_predeclared_row_when_all_samples_are_small():
    rows = [{'threshold': .15, 'n': 2, 'pf': 1.4, 'mean_net_return': .001},
            {'threshold': .25, 'n': 1, 'pf': None, 'mean_net_return': -.001}]
    chosen, passed = research.choose_threshold(rows)
    assert chosen is rows[0]
    assert chosen['n'] == 2 and not passed


def test_stress_null_pf_does_not_pass_or_raise():
    regular = {'trades': 100, 'pf': 1.5, 'final_equity': 110, 'drawdown': .1}
    stress = {'trades': 100, 'pf': None, 'final_equity': 101}
    assert not research.portfolio_pass(regular, stress)


def test_no_losing_labels_do_not_get_arbitrary_999_pf():
    result = research.stats(pd.DataFrame({'net_return': [.01, .02]}))
    assert result['pf'] == float('inf')
