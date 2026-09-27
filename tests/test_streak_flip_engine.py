import numpy as np
import pandas as pd
import pytest

from scripts.run_cross_sectional_momentum_research import (
    END,
    SPLIT_A,
    SPLIT_B,
    TRAIN_START,
)
from user_data.strategy_lib.streak_flip_engine import generate_events


def _impulse_frame(side: str) -> pd.DataFrame:
    date = pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC")
    open_ = np.full(len(date), 100.0)
    high = np.full(len(date), 100.2)
    low = np.full(len(date), 99.8)
    close = np.full(len(date), 100.0)
    if side == "long":
        open_[40:44] = [100, 97, 94, 91]
        close[40:44] = [97, 94, 91, 88]
        high[40:44] = open_[40:44] + .2
        low[40:44] = close[40:44] - .2
        open_[44], close[44], low[44], high[44] = 88, 92, 87.8, 92.2
    else:
        open_[40:44] = [100, 103, 106, 109]
        close[40:44] = [103, 106, 109, 112]
        high[40:44] = close[40:44] + .2
        low[40:44] = open_[40:44] - .2
        open_[44], close[44], low[44], high[44] = 112, 108, 107.8, 112.2
    return pd.DataFrame({"date": date, "open": open_, "high": high,
                         "low": low, "close": close})


@pytest.mark.parametrize("direction", ["long", "short"])
def test_detects_confirmed_impulse_flip(direction):
    events = generate_events("BTC-USDT-SWAP", _impulse_frame(direction))

    assert len(events) == 1
    assert events[0].date == pd.Timestamp("2026-01-01 03:45", tz="UTC")
    assert events[0].side == direction
    assert events[0].stop_price > 0


def test_future_prices_do_not_change_a_closed_signal():
    frame = _impulse_frame("long")
    original = generate_events("BTC-USDT-SWAP", frame)
    changed = frame.copy()
    changed.loc[changed.date > pd.Timestamp("2026-01-01 03:40", tz="UTC"),
                ["open", "high", "low", "close"]] *= 2

    revised = generate_events("BTC-USDT-SWAP", changed)

    assert revised[0].date == original[0].date
    assert revised[0].stop_price == pytest.approx(original[0].stop_price)


def test_requires_valid_ohlc_and_protocol_windows_are_fixed():
    frame = _impulse_frame("long")
    frame.loc[50, "low"] = frame.loc[50, "high"] + 1
    with pytest.raises(ValueError, match="invalid OHLC"):
        generate_events("BTC-USDT-SWAP", frame)

    assert TRAIN_START == pd.Timestamp("2026-03-01", tz="UTC")
    assert SPLIT_A == pd.Timestamp("2026-06-01", tz="UTC")
    assert SPLIT_B == pd.Timestamp("2026-07-15", tz="UTC")
    assert END == pd.Timestamp("2026-09-26", tz="UTC")
