"""Causal five-minute impulse-and-reversal signals for historical research."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


@dataclass(frozen=True)
class StreakFlipParameters:
    impulse_bars: int = 4
    minimum_impulse_atr: float = 1.5
    reversal_body_atr: float = 0.3
    stop_buffer_atr: float = 0.1
    minimum_risk_fraction: float = 0.004
    cooldown_bars: int = 6


def generate_events(
    pair: str,
    frame: pd.DataFrame,
    params: StreakFlipParameters = StreakFlipParameters(),
) -> list[EntryEvent]:
    """Enter after a strong four-candle run is broken by confirmation.

    All predicates and the stop use closed candles. Entries are at the next
    five-minute open; the returned list contains no future price information.
    """
    if (type(params.impulse_bars) is not int or params.impulse_bars < 2
            or type(params.cooldown_bars) is not int or params.cooldown_bars < 1
            or any(isinstance(value, bool) or not math.isfinite(value) or value <= 0
                   for value in (params.minimum_impulse_atr,
                                 params.reversal_body_atr,
                                 params.stop_buffer_atr,
                                 params.minimum_risk_fraction))):
        raise ValueError("invalid impulse, confirmation, stop or cooldown parameters")

    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("candles are missing required OHLC columns")
    raw = frame[["date", "open", "high", "low", "close"]].copy()
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
    raw = raw.sort_values("date").reset_index(drop=True)
    if raw.empty or raw["date"].duplicated().any():
        raise ValueError("candles must be nonempty and have unique dates")
    if not raw["date"].eq(raw["date"].dt.floor("5min")).all():
        raise ValueError("candles must align to five-minute boundaries")
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or (raw["high"] < raw[["open", "low", "close"]].max(axis=1)).any()
            or (raw["low"] > raw[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("candles contain invalid OHLC values")

    features = add_v2_indicators(raw)
    body = features.close - features.open
    span = features.high - features.low
    impulse_bars = params.impulse_bars
    prior_down = body.shift(1).lt(0).rolling(impulse_bars, min_periods=impulse_bars).sum().eq(impulse_bars)
    prior_up = body.shift(1).gt(0).rolling(impulse_bars, min_periods=impulse_bars).sum().eq(impulse_bars)
    down_move = features.open.shift(impulse_bars) - features.close.shift(1)
    up_move = features.close.shift(1) - features.open.shift(impulse_bars)
    down_impulse = prior_down & down_move.ge(params.minimum_impulse_atr * features.atr14)
    up_impulse = prior_up & up_move.ge(params.minimum_impulse_atr * features.atr14)
    upper_close = span.gt(0) & features.close.ge(features.high - span / 3)
    lower_close = span.gt(0) & features.close.le(features.low + span / 3)
    bullish_confirmation = (
        body.gt(0) & body.ge(params.reversal_body_atr * features.atr14)
        & features.close.gt(features.high.shift(1)) & upper_close
    )
    bearish_confirmation = (
        body.lt(0) & (-body).ge(params.reversal_body_atr * features.atr14)
        & features.close.lt(features.low.shift(1)) & lower_close
    )
    local_low = features.low.rolling(impulse_bars + 1, min_periods=impulse_bars + 1).min()
    local_high = features.high.rolling(impulse_bars + 1, min_periods=impulse_bars + 1).max()
    long_stop = local_low - params.stop_buffer_atr * features.atr14
    short_stop = local_high + params.stop_buffer_atr * features.atr14
    minimum_risk = params.minimum_risk_fraction * features.close
    long_signal = (down_impulse & bullish_confirmation
                   & (features.close - long_stop).ge(minimum_risk))
    short_signal = (up_impulse & bearish_confirmation
                    & (short_stop - features.close).ge(minimum_risk))
    continuous = features.date.diff().eq(pd.Timedelta(5 * 60 * 1_000_000_000, unit="ns"))
    continuous = continuous.rolling(impulse_bars, min_periods=impulse_bars).sum().eq(impulse_bars)

    signal_indices = np.flatnonzero(((long_signal | short_signal) & continuous).to_numpy())
    events: list[EntryEvent] = []
    next_allowed = 0
    for index in signal_indices:
        if index < next_allowed:
            continue
        side = "long" if bool(long_signal.iloc[index]) else "short"
        stop = float(long_stop.iloc[index] if side == "long" else short_stop.iloc[index])
        date_value = int(features.date.iloc[index].value) + 5 * 60 * 1_000_000_000
        date = pd.Timestamp(date_value, unit="ns", tz="UTC")
        events.append(EntryEvent(date, pair, side, stop, 1))
        next_allowed = index + params.cooldown_bars
    return events
