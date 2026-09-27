"""Causal contrarian events from previously observed extreme funding rates."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


LOOKBACK_SETTLEMENTS = 90
LOWER_QUANTILE = 0.10
UPPER_QUANTILE = 0.90
STOP_ATR = 1.5
MINIMUM_STOP_FRACTION = 0.004
TARGET_R = 1.5
HOLD_MINUTES = 240
STEP = pd.Timedelta(300_000_000_000, unit="ns")


def generate_events(
    pair: str,
    candles: pd.DataFrame,
    funding: pd.DataFrame,
) -> list[EntryEvent]:
    """Use settled funding only after settlement and enter on the following 5m open."""
    required_candles = {"date", "open", "high", "low", "close"}
    if not required_candles.issubset(candles.columns):
        raise ValueError("candles are missing required OHLC columns")
    if not {"date", "rate"}.issubset(funding.columns):
        raise ValueError("funding frame is missing date or rate")
    raw = candles[["date", "open", "high", "low", "close"]].copy()
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
    raw = raw.sort_values("date").reset_index(drop=True)
    if raw.empty or raw["date"].duplicated().any():
        raise ValueError("candles must be nonempty and have unique dates")
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or (raw["high"] < raw[["open", "low", "close"]].max(axis=1)).any()
            or (raw["low"] > raw[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("candles contain invalid OHLC values")

    observed = funding[["date", "rate"]].copy()
    observed["date"] = pd.to_datetime(observed["date"], utc=True).dt.as_unit("ns")
    observed["rate"] = observed["rate"].astype(float)
    observed = observed.sort_values("date").reset_index(drop=True)
    if (observed.empty or observed["date"].duplicated().any()
            or not np.isfinite(observed["rate"].to_numpy()).all()):
        raise ValueError("funding observations must be nonempty, unique and finite")
    if not observed["date"].diff().dropna().eq(pd.Timedelta(hours=8)).all():
        raise ValueError("funding observations must be complete 8-hour settlements")
    upper = observed["rate"].shift(1).rolling(
        LOOKBACK_SETTLEMENTS, min_periods=LOOKBACK_SETTLEMENTS,
    ).quantile(UPPER_QUANTILE)
    lower = observed["rate"].shift(1).rolling(
        LOOKBACK_SETTLEMENTS, min_periods=LOOKBACK_SETTLEMENTS,
    ).quantile(LOWER_QUANTILE)

    features = add_v2_indicators(raw)
    index_by_date = {int(date.value): index
                     for index, date in enumerate(pd.DatetimeIndex(features["date"]))}
    events: list[EntryEvent] = []
    for position, row in enumerate(observed.itertuples(index=False)):
        index = index_by_date.get(int(row.date.value))
        if index is None:
            raise ValueError("funding settlement has no matching candle")
        rate = float(row.rate)
        upper_cutoff = upper.iloc[position]
        lower_cutoff = lower.iloc[position]
        if math.isfinite(float(upper_cutoff)) and rate >= float(upper_cutoff):
            side = "short"
        elif math.isfinite(float(lower_cutoff)) and rate <= float(lower_cutoff):
            side = "long"
        else:
            continue
        close = float(features.at[index, "close"])
        atr = float(features.at[index, "atr14"])
        if not (math.isfinite(close) and close > 0 and math.isfinite(atr) and atr > 0):
            continue
        distance = max(STOP_ATR * atr, MINIMUM_STOP_FRACTION * close)
        stop_price = close + distance if side == "short" else close - distance
        if not math.isfinite(stop_price) or stop_price <= 0:
            continue
        events.append(EntryEvent(row.date + STEP, pair, side, stop_price, 1))
    return events
