import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.cross_sectional_momentum import (
    CrossSectionalMomentumParameters,
    generate_events,
)


def _frame(drift: float) -> pd.DataFrame:
    date = pd.date_range("2026-01-01", periods=72, freq="5min", tz="UTC")
    close = 100 * np.exp(np.arange(len(date)) * drift)
    return pd.DataFrame({
        "date": date,
        "open": close,
        "high": close * 1.001,
        "low": close * .999,
        "close": close,
        "volume": np.ones(len(date)),
        "quote_volume": np.ones(len(date)),
    })


def test_ranks_completed_returns_and_enters_next_rebalance_open():
    events = generate_events({"A": _frame(.001), "B": _frame(-.001)})

    assert events
    assert events[0].date == pd.Timestamp("2026-01-01 01:30", tz="UTC")
    at_first_entry = [event for event in events if event.date == events[0].date]
    assert {(event.pair, event.side) for event in at_first_entry} == {
        ("A", "long"), ("B", "short"),
    }
    assert all(event.stop_price > 0 for event in events)


def test_decision_is_causal_to_future_candle_changes():
    frames = {"A": _frame(.001), "B": _frame(-.001)}
    original = generate_events(frames)
    expected = original[0]
    decision_at = pd.Timestamp(expected.date.value - 5 * 60 * 1_000_000_000,
                               unit="ns", tz="UTC")
    changed = {}
    for pair, frame in frames.items():
        altered = frame.copy()
        future = altered.date > decision_at
        altered.loc[future, ["open", "high", "low", "close"]] *= 2
        changed[pair] = altered
    revised = generate_events(changed)

    match = next(event for event in revised
                 if (event.date, event.pair, event.side)
                 == (expected.date, expected.pair, expected.side))
    assert match.stop_price == pytest.approx(expected.stop_price)


def test_equal_cross_sectional_returns_do_not_create_orders():
    same = _frame(.0001)

    assert generate_events({"A": same, "B": same.copy()}) == []


def test_requires_at_least_two_symbols_and_hour_aligned_rebalance():
    with pytest.raises(ValueError, match="at least two"):
        generate_events({"A": _frame(.001)})
    with pytest.raises(ValueError, match="divide one hour"):
        generate_events(
            {"A": _frame(.001), "B": _frame(-.001)},
            CrossSectionalMomentumParameters(rebalance_bars=5),
        )
