"""Causal 24-hour range breakout continuation entries for offline research."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


LOOKBACK_BARS = 288
COOLDOWN_BARS = 12
TARGET_R = 2.0
HOLD_MINUTES = 240
BREAKOUT_ATR = 0.10
STOP_ATR = 1.25
MINIMUM_STOP_FRACTION = 0.004
MINIMUM_VOLUME_RATIO = 1.5
STEP = pd.Timedelta(300_000_000_000, unit="ns")


def generate_events(pair: str, frame: pd.DataFrame) -> list[EntryEvent]:
    """Return next-open events using only information available at signal close."""
    required = {"date", "open", "high", "low", "close", "quote_volume"}
    if not required.issubset(frame.columns):
        raise ValueError("candles are missing breakout inputs")
    raw = frame[["date", "open", "high", "low", "close", "quote_volume"]].copy()
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
    raw = raw.sort_values("date").reset_index(drop=True)
    if raw.empty or raw["date"].duplicated().any():
        raise ValueError("candles must be nonempty and have unique dates")
    if not raw["date"].eq(raw["date"].dt.floor("5min")).all():
        raise ValueError("candles must align to five-minute boundaries")
    values = raw[["open", "high", "low", "close", "quote_volume"]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any()
            or (values[:, 4] < 0).any()
            or (raw["high"] < raw[["open", "low", "close"]].max(axis=1)).any()
            or (raw["low"] > raw[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("candles contain invalid OHLC or quote volume")

    features = add_v2_indicators(raw)
    atr = features["atr14"]
    prior_high = features["high"].shift(1).rolling(LOOKBACK_BARS, min_periods=LOOKBACK_BARS).max()
    prior_low = features["low"].shift(1).rolling(LOOKBACK_BARS, min_periods=LOOKBACK_BARS).min()
    upper = prior_high + BREAKOUT_ATR * atr
    lower = prior_low - BREAKOUT_ATR * atr
    volume_baseline = features["quote_volume"].shift(1).rolling(20, min_periods=20).median()
    volume_ok = features["quote_volume"].ge(MINIMUM_VOLUME_RATIO * volume_baseline)
    long_trend = (
        features["ema20"].gt(features["ema60"])
        & features["ema60"].gt(features["ema120"])
        & features["ema60_slope3"].gt(0)
        & features["close"].gt(features["ema20"])
    )
    short_trend = (
        features["ema20"].lt(features["ema60"])
        & features["ema60"].lt(features["ema120"])
        & features["ema60_slope3"].lt(0)
        & features["close"].lt(features["ema20"])
    )
    crossed_up = features["close"].gt(upper) & features["close"].shift(1).le(upper.shift(1))
    crossed_down = features["close"].lt(lower) & features["close"].shift(1).ge(lower.shift(1))
    continuous = features["date"].diff().eq(STEP)
    long_signal = long_trend & crossed_up & volume_ok & continuous
    short_signal = short_trend & crossed_down & volume_ok & continuous

    events: list[EntryEvent] = []
    next_allowed = 0
    for index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        if index < next_allowed:
            continue
        side = "long" if bool(long_signal.iloc[index]) else "short"
        close = float(features.at[index, "close"])
        stop_distance = max(STOP_ATR * float(atr.iloc[index]), MINIMUM_STOP_FRACTION * close)
        if not math.isfinite(stop_distance) or stop_distance <= 0:
            continue
        stop_price = close - stop_distance if side == "long" else close + stop_distance
        if not math.isfinite(stop_price) or stop_price <= 0:
            continue
        events.append(EntryEvent(features.at[index, "date"] + STEP, pair, side, stop_price, 1))
        next_allowed = index + COOLDOWN_BARS
    return events
