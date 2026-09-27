import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.liquidity_sweep import (
    LiquiditySweepParameters,
    generate_events,
)


def _frame(side="long", count=100, signal_at=70):
    open_ = np.full(count, 100.0)
    high = np.full(count, 100.5)
    low = np.full(count, 99.5)
    close = np.full(count, 100.0)
    if side == "long":
        open_[signal_at] = 99.2
        high[signal_at] = 100.15
        low[signal_at] = 99.1
        close[signal_at] = 100.0
    elif side == "short":
        open_[signal_at] = 100.8
        high[signal_at] = 100.9
        low[signal_at] = 99.85
        close[signal_at] = 100.0
    else:
        raise ValueError(side)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=count, freq="5min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
    })


@pytest.mark.parametrize("side", ["long", "short"])
def test_range_sweep_reclaim_emits_next_bar_entry(side):
    frame = _frame(side)
    events = generate_events("TEST", frame)

    assert len(events) == 1
    assert events[0].date == frame.date.iloc[71]
    assert events[0].side == side
    assert (events[0].stop_price < 100.0) == (side == "long")


def test_future_candles_do_not_change_sweep_event():
    frame = _frame()
    original = generate_events("TEST", frame)
    changed = frame.copy()
    changed.loc[71:, ["open", "high", "low", "close"]] *= 1.2

    assert [event for event in original if event.date == frame.date.iloc[71]] == [
        event for event in generate_events("TEST", changed)
        if event.date == frame.date.iloc[71]
    ]


def test_bars_sweeping_both_range_edges_are_not_traded():
    frame = _frame()
    frame.loc[70, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]

    assert generate_events("TEST", frame) == []


def test_rejects_duplicate_dates():
    frame = _frame()
    frame.loc[1, "date"] = frame.loc[0, "date"]

    with pytest.raises(ValueError, match="unique"):
        generate_events("TEST", frame)


def test_twelve_hour_mode_uses_older_range_boundary():
    frame = _frame(count=220, signal_at=180)
    frame.loc[40, "low"] = 95.0

    assert len(generate_events(
        "TEST", frame, LiquiditySweepParameters(lookback_bars=24)
    )) == 1
    assert generate_events(
        "TEST", frame, LiquiditySweepParameters(lookback_bars=144)
    ) == []
