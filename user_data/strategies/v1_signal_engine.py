from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


SIX_AVERAGE_COLUMNS = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


@dataclass(frozen=True)
class V1Parameters:
    compression_threshold: float = 0.01
    breakout_threshold: float = 0.0025
    pullback_tolerance: float = 0.005
    pullback_max_candles: int = 12


def add_six_averages(dataframe: pd.DataFrame) -> pd.DataFrame:
    result = dataframe.copy()
    result["ma20"] = result["close"].rolling(20).mean()
    result["ma60"] = result["close"].rolling(60).mean()
    result["ma120"] = result["close"].rolling(120).mean()
    result["ema20"] = result["close"].ewm(span=20, adjust=False).mean()
    result["ema60"] = result["close"].ewm(span=60, adjust=False).mean()
    result["ema120"] = result["close"].ewm(span=120, adjust=False).mean()
    return result


def scan_v1_setups(dataframe: pd.DataFrame, params: V1Parameters) -> pd.DataFrame:
    result = dataframe.copy()
    if any(column not in result for column in SIX_AVERAGE_COLUMNS):
        result = add_six_averages(result)

    averages = result.loc[:, SIX_AVERAGE_COLUMNS]
    result["compression"] = (averages.max(axis=1) - averages.min(axis=1)) / result["close"]
    result["enter_long"] = 0
    result["enter_short"] = 0
    result["initial_stop_price"] = np.nan
    result["risk_distance_ratio"] = np.nan
    result["setup_state"] = "idle"

    state = "idle"
    zone_low: float | None = None
    zone_high: float | None = None
    breakout_index: int | None = None

    for position in range(len(result)):
        row = result.iloc[position]
        average_row = averages.iloc[position]
        if average_row.isna().any() or row["close"] <= 0:
            continue

        if state == "long_pending":
            assert zone_low is not None and zone_high is not None and breakout_index is not None
            age = position - breakout_index
            touched = row["low"] <= zone_high * (1 + params.pullback_tolerance)
            held = row["close"] >= zone_high * (1 - params.pullback_tolerance)
            if age > params.pullback_max_candles or row["close"] < zone_low:
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            elif touched and held and row["close"] > zone_low:
                label = result.index[position]
                result.at[label, "enter_long"] = 1
                result.at[label, "initial_stop_price"] = zone_low
                result.at[label, "risk_distance_ratio"] = (
                    row["close"] - zone_low
                ) / row["close"]
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            elif touched:
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            result.at[result.index[position], "setup_state"] = state
            continue

        if state == "short_pending":
            assert zone_low is not None and zone_high is not None and breakout_index is not None
            age = position - breakout_index
            touched = row["high"] >= zone_low * (1 - params.pullback_tolerance)
            held = row["close"] <= zone_low * (1 + params.pullback_tolerance)
            if age > params.pullback_max_candles or row["close"] > zone_high:
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            elif touched and held and row["close"] < zone_high:
                label = result.index[position]
                result.at[label, "enter_short"] = 1
                result.at[label, "initial_stop_price"] = zone_high
                result.at[label, "risk_distance_ratio"] = (
                    zone_high - row["close"]
                ) / row["close"]
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            elif touched:
                state = "idle"
                zone_low = zone_high = None
                breakout_index = None
            result.at[result.index[position], "setup_state"] = state
            continue

        current_low = float(average_row.min())
        current_high = float(average_row.max())
        if zone_high is not None and row["close"] > zone_high * (1 + params.breakout_threshold):
            state = "long_pending"
            breakout_index = position
        elif zone_low is not None and row["close"] < zone_low * (1 - params.breakout_threshold):
            state = "short_pending"
            breakout_index = position
        elif result["compression"].iloc[position] <= params.compression_threshold:
            zone_low = current_low
            zone_high = current_high

        result.at[result.index[position], "setup_state"] = state
    return result
