"""One closed-bar continuation confirmation of the fixed breakout/retest setup.

The returned frame is compatible with ``profit_events``. ``min_atr_pct`` is a
fraction of close (0.001 means 0.1%); neither this filter nor the underlying scan
reads an execution candle's price to decide its preceding entry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from user_data.strategy_lib.profit_signal_engine import ProfitParameters, scan_profit


BASE_PARAMETERS = ProfitParameters(
    family='breakout_retest', fast=20, slow=60, volume_multiple=1.,
    stop_buffer_atr=.3, cooldown_bars=12,
)


def scan_confirmed_retest(frame: pd.DataFrame, stop_atr: float,
                          min_atr_pct: float = 0.) -> pd.DataFrame:
    """Confirm only the immediately following contiguous closed five-minute bar.

    Equality at the original stop is a touch and rejects the setup. Equality at
    the signal high/low does not confirm. Stops can widen, never tighten. Call
    ``profit_events`` on the result to create next-open-time ``EntryEvent`` values.
    """
    if (not np.isfinite(stop_atr) or stop_atr <= 0
            or not np.isfinite(min_atr_pct) or min_atr_pct < 0):
        raise ValueError('stop_atr must be positive and min_atr_pct nonnegative')
    original = scan_profit(frame, BASE_PARAMETERS)
    confirmed = original.copy()
    confirmed['side'] = ''
    confirmed['initial_stop_price'] = np.nan
    step = pd.Timedelta(minutes=5)
    for i in range(1, len(original)):
        signal, current = original.iloc[i - 1], original.iloc[i]
        if signal.side not in {'long', 'short'} or current.date - signal.date != step:
            continue
        values = [signal.initial_stop_price, signal.high, signal.low,
                  current.open, current.high, current.low, current.close, current.atr14]
        if not np.isfinite(values).all() or any(value <= 0 for value in values):
            continue
        if (current.high < max(current.open, current.close)
                or current.low > min(current.open, current.close)
                or current.atr14 / current.close < min_atr_pct):
            continue
        stop = float(signal.initial_stop_price)
        if signal.side == 'long':
            if current.close <= signal.high or current.low <= stop:
                continue
            widened = min(stop, current.close - stop_atr * current.atr14)
        else:
            if current.close >= signal.low or current.high >= stop:
                continue
            widened = max(stop, current.close + stop_atr * current.atr14)
        if not np.isfinite(widened) or widened <= 0:
            continue
        idx = confirmed.index[i]
        confirmed.at[idx, 'side'] = signal.side
        confirmed.at[idx, 'initial_stop_price'] = widened
    return confirmed
