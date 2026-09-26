"""Frozen, causal 5-minute entry families for the profitability research study.

Dates label candle opens. Decisions use closed candles and EntryEvent.date is the
following candle's open time; its price is deliberately unavailable to this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import add_v2_indicators
from user_data.strategy_lib.v2_backtester import EntryEvent


@dataclass(frozen=True)
class ProfitParameters:
    family: str = 'breakout_retest'
    fast: int = 9
    slow: int = 21
    volume_multiple: float = 1.0
    stop_buffer_atr: float = .15
    cooldown_bars: int = 12


FAMILIES = {'breakout_retest', 'trend_pullback', 'sweep_reclaim', 'range_reversion'}


def _higher(raw: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Discard incomplete buckets, including buckets containing a missing candle."""
    grouped = raw.set_index('date').resample(f'{minutes}min', closed='left', label='left')
    bars = grouped.agg({'open': 'first', 'high': 'max', 'low': 'min',
                        'close': 'last', 'volume': 'sum', 'quote_volume': 'sum'})
    bars = bars.loc[grouped.size().eq(minutes // 5)].reset_index()
    bars = add_v2_indicators(bars)
    bars[f'bias{minutes}'] = (bars.close.ewm(span=9, adjust=False, min_periods=9).mean()
                               - bars.close.ewm(span=21, adjust=False, min_periods=21).mean())
    # After missing higher candles, demand a fresh continuous 21-candle history.
    valid = bars.date.diff().eq(pd.Timedelta(minutes=minutes)).rolling(20, min_periods=20).sum().eq(20)
    bars.loc[~valid, f'bias{minutes}'] = np.nan
    bars[f'available{minutes}'] = bars.date + pd.Timedelta(minutes=minutes)
    return bars[[f'available{minutes}', f'bias{minutes}', 'atr14']].rename(columns={'atr14': f'atr{minutes}'})


def scan_profit(frame: pd.DataFrame, params: ProfitParameters) -> pd.DataFrame:
    if params.family not in FAMILIES or not 0 < params.fast < params.slow:
        raise ValueError('invalid family or EMA lengths')
    if (not np.isfinite(params.volume_multiple) or params.volume_multiple <= 0
            or not np.isfinite(params.stop_buffer_atr) or params.stop_buffer_atr <= 0
            or not isinstance(params.cooldown_bars, int) or params.cooldown_bars < 12):
        raise ValueError('invalid volume, buffer or cooldown (minimum 60 minutes)')
    raw = frame[['date', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']].copy()
    raw['date'] = pd.to_datetime(raw.date, utc=True).dt.as_unit('ns')
    raw = raw.sort_values('date').drop_duplicates('date', keep='last').reset_index(drop=True)
    if not raw.date.eq(raw.date.dt.floor('5min')).all():
        raise ValueError('candles must align to five-minute boundaries')
    f = add_v2_indicators(raw)
    f['fast'] = f.close.ewm(span=params.fast, adjust=False, min_periods=params.fast).mean()
    f['slow'] = f.close.ewm(span=params.slow, adjust=False, min_periods=params.slow).mean()
    f['compression'] = (f.fast - f.slow).abs() / f.atr14
    f['decision_at'] = f.date + pd.Timedelta(minutes=5)
    for minutes in (15, 60):
        f = pd.merge_asof(f, _higher(raw, minutes), left_on='decision_at',
                          right_on=f'available{minutes}', direction='backward',
                          tolerance=pd.Timedelta(minutes=minutes - 5))
    compressed = f.compression.le(.8)
    coil = compressed.rolling(5, min_periods=5).sum().ge(3).shift(1, fill_value=False)
    recent = compressed.rolling(6, min_periods=6).max().shift(1).fillna(0).astype(bool)
    upper6 = f.high.rolling(6, min_periods=6).max().shift(1)
    lower6 = f.low.rolling(6, min_periods=6).min().shift(1)
    upper12 = f.high.rolling(12, min_periods=12).max().shift(1)
    lower12 = f.low.rolling(12, min_periods=12).min().shift(1)
    low3 = f.low.rolling(3, min_periods=3).min()
    high3 = f.high.rolling(3, min_periods=3).max()
    median_volume = f.volume.rolling(20, min_periods=20).median().shift(1)
    continuous = f.date.diff().eq(pd.Timedelta(minutes=5)).rolling(119, min_periods=119).sum().eq(119)
    f['side'] = ''
    f['initial_stop_price'] = np.nan
    pending = None
    next_index = 0
    for i, row in enumerate(f.itertuples()):
        if (not continuous.iloc[i] or not np.isfinite(row.atr14) or row.atr14 <= 0
                or not np.isfinite(row.bias15) or not np.isfinite(row.bias60)):
            pending = None
            continue
        if i < next_index:
            pending = None
            continue
        atr = row.atr14
        buffer = params.stop_buffer_atr * atr
        body = row.close - row.open
        span = row.high - row.low
        upper_close = span > 0 and row.close >= row.high - span / 3
        lower_close = span > 0 and row.close <= row.low + span / 3
        long_trend = row.bias15 > 0 and row.bias60 > 0
        short_trend = row.bias15 < 0 and row.bias60 < 0
        liquid = row.volume >= params.volume_multiple * median_volume.iloc[i]
        side, stop = '', np.nan
        if params.family == 'breakout_retest':
            if pending is not None:
                direction, boundary, started = pending
                pending = None
                # First touch either confirms or terminates this setup. A close
                # through the boundary / loss of trend cancels before entry.
                if i - started <= 6:
                    if direction == 'long' and long_trend and row.fast > row.slow and row.close > boundary:
                        if row.low <= boundary:
                            side, stop = 'long', min(row.low, boundary) - buffer
                        else:
                            pending = (direction, boundary, started)
                    elif direction == 'short' and short_trend and row.fast < row.slow and row.close < boundary:
                        if row.high >= boundary:
                            side, stop = 'short', max(row.high, boundary) + buffer
                        else:
                            pending = (direction, boundary, started)
                # Never recycle the cancellation candle as a fresh breakout.
            elif coil.iloc[i] and liquid:
                if long_trend and row.fast > row.slow and body >= .4 * atr and row.close >= upper6.iloc[i] + .15 * atr:
                    pending = ('long', upper6.iloc[i], i)
                elif short_trend and row.fast < row.slow and body <= -.4 * atr and row.close <= lower6.iloc[i] - .15 * atr:
                    pending = ('short', lower6.iloc[i], i)
        elif params.family == 'trend_pullback' and liquid:
            if long_trend and row.fast > row.slow and row.low <= row.fast < row.close <= row.fast + .5 * atr and body > 0 and upper_close:
                side, stop = 'long', low3.iloc[i] - buffer
            elif short_trend and row.fast < row.slow and row.high >= row.fast > row.close >= row.fast - .5 * atr and body < 0 and lower_close:
                side, stop = 'short', high3.iloc[i] + buffer
        elif params.family == 'sweep_reclaim' and recent.iloc[i] and liquid:
            if row.bias15 > 0 and row.low < lower12.iloc[i] - .1 * atr and row.close > lower12.iloc[i] and upper_close:
                side, stop = 'long', row.low - buffer
            elif row.bias15 < 0 and row.high > upper12.iloc[i] + .1 * atr and row.close < upper12.iloc[i] and lower_close:
                side, stop = 'short', row.high + buffer
        elif params.family == 'range_reversion' and recent.iloc[i] and liquid:
            weak = abs(row.bias15) <= .5 * row.atr15 and abs(row.bias60) <= .5 * row.atr60
            previous = f.iloc[i - 1]
            if weak and previous.close <= previous.slow - previous.atr14 and body > 0 and upper_close:
                side, stop = 'long', low3.iloc[i] - buffer
            elif weak and previous.close >= previous.slow + previous.atr14 and body < 0 and lower_close:
                side, stop = 'short', high3.iloc[i] + buffer
        if side and ((side == 'long' and stop < row.close) or (side == 'short' and stop > row.close)):
            f.at[i, 'side'] = side
            f.at[i, 'initial_stop_price'] = float(stop)
            next_index = i + params.cooldown_bars
            pending = None
    return f


def profit_events(pair: str, signals: pd.DataFrame, target_r: float,
                  cost_multiple: float = 2.0, round_trip_cost: float = .002) -> list[EntryEvent]:
    """Require target headroom using the decision close, never next-open prices."""
    if (not np.isfinite(target_r) or target_r <= 0 or not np.isfinite(cost_multiple)
            or cost_multiple < 0 or not np.isfinite(round_trip_cost) or round_trip_cost < 0):
        raise ValueError('invalid target or costs')
    events = []
    for row in signals.loc[signals.side.ne('')].itertuples():
        if row.side not in {'long', 'short'} or not np.isfinite(row.close) or row.close <= 0:
            continue
        stop = float(row.initial_stop_price)
        distance = row.close - stop if row.side == 'long' else stop - row.close
        if not np.isfinite(distance) or distance <= 0:
            continue
        if distance / row.close * target_r < cost_multiple * round_trip_cost:
            continue
        events.append(EntryEvent(pd.Timestamp(row.decision_at), pair, row.side, stop, 1))
    return events
