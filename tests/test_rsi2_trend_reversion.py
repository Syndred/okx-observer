import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.rsi2_trend_reversion import (
    RSI2TrendReversionParameters,
    generate_events,
)


def _frame(side="long", count=390, signal_at=362):
    index = np.arange(count)
    base = 100 + index * .1 if side == "long" else 200 - index * .1
    close = base.astype(float)
    if side == "long":
        close[signal_at - 1] = base[signal_at - 1] - .3
        close[signal_at] = base[signal_at] - .7
    elif side == "short":
        close[signal_at - 1] = base[signal_at - 1] + .3
        close[signal_at] = base[signal_at] + .7
    else:
        raise ValueError(side)
    open_ = close.copy()
    high = close + .1
    low = close - .1
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=count, freq="5min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
    })


@pytest.mark.parametrize("side", ["long", "short"])
def test_rsi2_extreme_emits_next_bar_entry_in_aligned_trend(side):
    frame = _frame(side)
    events = generate_events("TEST", frame)

    assert len(events) == 1
    assert events[0].date == frame.date.iloc[363]
    assert events[0].side == side
    assert events[0].rank == 1
    assert (events[0].stop_price < frame.close.iloc[362]) == (side == "long")


def test_future_candles_do_not_change_rsi2_signal():
    frame = _frame(count=410)
    original = generate_events("TEST", frame)
    changed = frame.copy()
    changed.loc[363:, ["open", "high", "low", "close"]] *= 1.5

    assert [event for event in original if event.date == frame.date.iloc[363]] == [
        event for event in generate_events("TEST", changed)
        if event.date == frame.date.iloc[363]
    ]


def test_rejects_invalid_rsi_thresholds():
    frame = _frame()
    with pytest.raises(ValueError, match="RSI"):
        generate_events("TEST", frame,
                        params=RSI2TrendReversionParameters(oversold=95))
