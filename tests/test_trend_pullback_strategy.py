import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.trend_pullback import (
    TrendPullbackParameters,
    generate_events,
)


def _frame(side="long", count=390):
    index = np.arange(count)
    base = 100 + index * .1 if side == "long" else 200 - index * .1
    close = base.astype(float)
    target = 362
    if side == "long":
        close[target - 3:target] = [base[target - 3] - .25,
                                    base[target - 2] - .55,
                                    base[target - 1] - .9]
        close[target] = close[target - 1] + .8
    else:
        close[target - 3:target] = [base[target - 3] + .25,
                                    base[target - 2] + .55,
                                    base[target - 1] + .9]
        close[target] = close[target - 1] - .8
    open_ = close.copy()
    open_[target - 3:target] = close[target - 3:target] + (.05 if side == "short" else -.05)
    open_[target] = close[target - 1]
    high = np.maximum(open_, close) + .1
    low = np.minimum(open_, close) - .1
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=count, freq="5min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
    })


@pytest.mark.parametrize("side", ["long", "short"])
def test_confirmed_pullback_emits_causal_event(side):
    frame = _frame(side)
    events = generate_events("TEST", frame)

    assert len(events) == 1
    assert events[0].date == frame.date.iloc[362] + pd.Timedelta(300_000_000_000, unit="ns")
    assert events[0].side == side
    assert events[0].rank == 1
    assert (events[0].stop_price < frame.close.iloc[362]) == (side == "long")


def test_future_candles_do_not_change_existing_pullback_event():
    frame = _frame(count=410)
    original = generate_events("TEST", frame)
    changed = frame.copy()
    changed.loc[363:, ["open", "high", "low", "close"]] *= 1.5

    assert [event for event in original if event.date == frame.date.iloc[363]] == [
        event for event in generate_events("TEST", changed)
        if event.date == frame.date.iloc[363]
    ]


def test_rejects_invalid_cadence():
    frame = _frame()
    with pytest.raises(ValueError, match="parameters"):
        generate_events("TEST", frame,
                        params=TrendPullbackParameters(cadence_minutes=7))
