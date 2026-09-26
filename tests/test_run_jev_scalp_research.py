"""Research selection, split purging, causal Jev state and execution contracts."""

from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest

from scripts import run_jev_scalp_research as research
from user_data.strategy_lib.scalp_signal_engine import ScalpParameters
from user_data.strategy_lib.v2_backtester import EntryEvent


def summary(**changes):
    return {"n": 100, "win_rate": .51, "pf": 1.1,
            "mean_net_return": .0001, **changes}


def source():
    rng = np.random.default_rng(31)
    close = 100 + np.cumsum(rng.normal(.015, .14, 180))
    return pd.DataFrame({
        "date": pd.date_range("2026-07-31", periods=180, freq="5min", tz="UTC"),
        "open": close - .03, "high": close + .10, "low": close - .13,
        "close": close, "volume": np.linspace(10, 20, 180),
        "quote_volume": np.linspace(1000, 2000, 180),
    })


def entry(data, index=150):
    return EntryEvent(data.date.iloc[index], "SECRET_PAIR/USDT", "long",
                      float(data.close.iloc[index - 1]) - 1.5, 1)


def test_stats_empty_has_no_fabricated_win_rate():
    assert research.stats(pd.DataFrame()) == {
        "n": 0, "win_rate": None, "pf": None, "mean_net_return": None,
    }


def test_stats_uses_net_returns_and_zero_is_not_a_win():
    # An inconsistent 'won' column cannot override actual after-cost profits.
    result = research.stats(pd.DataFrame({"net_return": [.04, -.02, 0], "won": [True] * 3}))
    assert result["n"] == 3
    assert result["win_rate"] == pytest.approx(1 / 3)
    assert result["pf"] == pytest.approx(2)
    assert result["mean_net_return"] == pytest.approx(.02 / 3)


@pytest.mark.parametrize("returns,pf,win_rate", [
    ([.01, .02], 999., 1.), ([0, 0], 0., 0.), ([-.01, -.02], 0., 0.),
])
def test_stats_degenerate_samples(returns, pf, win_rate):
    result = research.stats(pd.DataFrame({"net_return": returns}))
    assert result["pf"] == pf
    assert result["win_rate"] == win_rate


@pytest.mark.parametrize("changes,expected", [
    ({}, True), ({"n": 99}, False), ({"n": 100}, True),
    ({"win_rate": .5}, False), ({"win_rate": .500001}, True),
    ({"pf": 1.099999}, False), ({"pf": 1.1}, True),
    ({"mean_net_return": 0}, False), ({"mean_net_return": -.0001}, False),
])
def test_gate_has_minimum_sample_strict_majority_and_profit_factor(changes, expected):
    assert research.gate(summary(**changes), 100) is expected


def test_gate_and_rank_handle_empty_samples_without_comparing_none():
    empty = research.stats(pd.DataFrame())
    assert not research.gate(empty, 50)
    assert research.rank(empty, 50) == (-1, 0, 0.)


def test_rank_prioritizes_valid_sample_then_gate_then_profit_factor():
    too_small = summary(n=49, win_rate=1., pf=999.)
    failed = summary(n=50, win_rate=.5, pf=100.)
    passed = summary(n=50, win_rate=.51, pf=1.1)
    higher_pf = summary(n=50, win_rate=.51, pf=1.2)
    assert research.rank(too_small, 50) < research.rank(failed, 50)
    assert research.rank(failed, 50) < research.rank(passed, 50)
    assert research.rank(passed, 50) < research.rank(higher_pf, 50)


def test_period_events_purges_full_holding_interval_at_boundaries():
    start = pd.Timestamp("2026-08-01", tz="UTC")
    end = start + pd.Timedelta(hours=1)
    events = [EntryEvent(start + pd.Timedelta(minutes=m), "A", "long", 99, 1)
              for m in [-5, 0, 40, 45, 50, 60]]
    selected = research.period_events(events, start, end, 15)
    assert selected == events[1:4]
    assert selected[-1].date + pd.Timedelta(minutes=15) == end
    next_period = research.period_events(events, end, end + pd.Timedelta(hours=1), 15)
    assert next_period == [events[-1]]
    assert set(selected).isdisjoint(next_period)


def test_scalp_state_cannot_see_entry_candle_or_later_data():
    data = source()
    signal = entry(data)
    params = ScalpParameters()
    expected = research.scalp_state(signal, data.iloc[:150], params, .75, 15)
    changed = data.copy()
    changed.loc[150:, ["open", "high", "low", "close", "volume", "quote_volume"]] *= 10000
    assert research.scalp_state(signal, data, params, .75, 15) == expected
    assert research.scalp_state(signal, changed, params, .75, 15) == expected
    assert expected["bars_5m_oldest_first"][-1]["close"] == 0.
    assert len(expected["bars_5m_oldest_first"]) == 24


def test_scalp_state_omits_identifying_pair_and_timestamps():
    data = source()
    signal = entry(data)
    state = research.scalp_state(signal, data, ScalpParameters(), .75, 15)
    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)
    assert not {"pair", "date", "timestamp", "entry_date"}.intersection(keys(state))
    encoded = json.dumps(state, allow_nan=False)
    assert signal.pair not in encoded
    assert signal.date.isoformat() not in encoded
    assert "2026-07-31" not in encoded
    renamed = replace(signal, pair="ANOTHER/USDT")
    assert research.scalp_state(renamed, data, ScalpParameters(), .75, 15) == state


@pytest.mark.parametrize("case", ["short_warmup", "missing_last_closed_bar"])
def test_scalp_state_rejects_insufficient_closed_history(case):
    data = source()
    signal = entry(data, index=100 if case == "short_warmup" else 150)
    if case == "missing_last_closed_bar":
        data = data.drop(index=149)
    with pytest.raises(ValueError, match="incomplete past state"):
        research.scalp_state(signal, data, ScalpParameters(), .75, 15)


def test_scalp_state_execution_description_matches_cost_and_fill_conventions():
    data = source()
    signal = entry(data)
    state = research.scalp_state(signal, data, ScalpParameters(), .75, 30)
    rules = state["execution_rules"]
    assert rules["target_r"] == .75
    assert rules["maximum_holding_minutes"] == 30
    assert rules["fee_each_side"] == rules["slippage_each_side"] == .0005
    assert "next 5m open" in rules["entry"] and "not known yet" in rules["entry"]
    assert "actual slipped entry fill" in rules["target_definition"]
    assert "close of last 5m bar" in rules["time_exit"]
    assert "stop first" in rules["same_bar_ambiguity"]
    assert "worse open" in rules["same_bar_ambiguity"]
    assert "0.01%" in rules["funding"] and "already open" in rules["funding"]
    assert "strictly positive net profit after all costs" in rules["success"]
    reference = float(data.close.iloc[149])
    assert state["stop_distance_fraction"] == pytest.approx(abs(reference - signal.stop_price) / reference)
    assert str(round(signal.stop_price / reference - 1, 7)) in rules["stop"]


@pytest.mark.parametrize("stress,cost,funding", [(False, .0005, .0001), (True, .001, .0002)])
def test_portfolio_passes_frozen_execution_parameters_to_simulator(monkeypatch, stress, cost, funding):
    captured = {}
    sentinel = object()
    def simulate(frames, events, options, **kwargs):
        captured.update(frames=frames, events=events, options=options, **kwargs)
        return sentinel
    monkeypatch.setattr(research, "simulate_portfolio", simulate)
    frames, events = {"A": pd.DataFrame()}, []
    start, end = pd.Timestamp("2026-08-01", tz="UTC"), pd.Timestamp("2026-08-02", tz="UTC")
    assert research.portfolio(frames, events, .75, 30, start, end, stress) is sentinel
    assert captured["frames"] is frames and captured["events"] is events
    assert captured["start"] == start and captured["end"] == end
    options = captured["options"]
    assert options.exit_mode == "fixed3"
    assert options.take_profit_r == .75 and options.max_hold_minutes == 30
    assert options.candle_minutes == 5
    assert options.fee_rate == options.slippage_rate == cost
    assert options.missing_funding_rate_per_8h == funding
    assert not options.pyramid_enabled
