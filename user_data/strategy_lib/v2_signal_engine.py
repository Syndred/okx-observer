"""Causal multi-timeframe signal engine for the OKX short-line V2 study."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


SIX_AVERAGES = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


@dataclass(frozen=True)
class V2Parameters:
    compression_atr: float = 0.60
    breakout_atr: float = 0.10
    pullback_atr: float = 0.20
    pullback_wait_15m: int = 12
    stop_buffer_atr: float = 0.10
    compressed_zone_max_age_1h: int = 6
    strict_market_consensus: bool = False


def add_v2_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add only the six averages and ATR needed by the original system."""
    output = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    close = output["close"].astype(float)
    for period in (20, 60, 120):
        ma_column = f"ma{period}"
        ema_column = f"ema{period}"
        if ma_column not in output:
            output[ma_column] = close.rolling(period, min_periods=period).mean()
        if ema_column not in output:
            output[ema_column] = close.ewm(span=period, adjust=False, min_periods=period).mean()
    if "atr14" not in output:
        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                output["high"] - output["low"],
                (output["high"] - previous_close).abs(),
                (output["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        output["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    if "ema60_slope3" not in output:
        output["ema60_slope3"] = output["ema60"] - output["ema60"].shift(3)
    if "trend_long" not in output:
        output["trend_long"] = (
            (output["close"] > output["ema120"])
            & (output["ema20"] > output["ema60"])
            & (output["ema60"] > output["ema120"])
            & (output["ema60_slope3"] > 0)
        )
    if "trend_short" not in output:
        output["trend_short"] = (
            (output["close"] < output["ema120"])
            & (output["ema20"] < output["ema60"])
            & (output["ema60"] < output["ema120"])
            & (output["ema60_slope3"] < 0)
        )
    return output.reset_index(drop=True)


def _informative(frame: pd.DataFrame, timeframe: str, prefix: str) -> pd.DataFrame:
    hours = {"1h": 1, "4h": 4}[timeframe]
    source = add_v2_indicators(frame)
    source["available_at"] = pd.to_datetime(source["date"], utc=True) + pd.Timedelta(hours=hours)
    if timeframe == "1h":
        source["cluster_high"] = source.loc[:, SIX_AVERAGES].max(axis=1)
        source["cluster_low"] = source.loc[:, SIX_AVERAGES].min(axis=1)
        source["compression"] = (
            (source["cluster_high"] - source["cluster_low"]) / source["atr14"]
        )
        columns = [
            "available_at",
            "date",
            "close",
            "atr14",
            "cluster_high",
            "cluster_low",
            "compression",
        ]
    else:
        columns = ["available_at", "date", "trend_long", "trend_short"]
    output = source.loc[:, columns].rename(
        columns={column: f"{prefix}_{column}" for column in columns if column != "available_at"}
    )
    return output.sort_values("available_at")


def _merge_informative(
    base: pd.DataFrame, frame: pd.DataFrame, timeframe: str, prefix: str
) -> pd.DataFrame:
    return pd.merge_asof(
        base.sort_values("date"),
        _informative(frame, timeframe, prefix),
        left_on="date",
        right_on="available_at",
        direction="backward",
        allow_exact_matches=True,
    ).drop(columns="available_at")


def _market_allowed(row: pd.Series, side: str, strict: bool) -> bool:
    btc_same = _flag(row.get(f"btc_trend_{side}", False))
    eth_same = _flag(row.get(f"eth_trend_{side}", False))
    opposite = "short" if side == "long" else "long"
    btc_opposed = _flag(row.get(f"btc_trend_{opposite}", False))
    eth_opposed = _flag(row.get(f"eth_trend_{opposite}", False))
    if strict:
        return btc_same and eth_same
    return (btc_same or eth_same) and not btc_opposed and not eth_opposed


def _flag(value: object) -> bool:
    return bool(value) if pd.notna(value) else False


def _universe_hours(universe_mask: pd.DataFrame, pair: str) -> set[pd.Timestamp]:
    if universe_mask.empty:
        return set()
    source = universe_mask.loc[universe_mask["pair"] == pair].copy()
    if "eligible" in source:
        source = source.loc[source["eligible"].fillna(False)]
    return set(pd.to_datetime(source["date"], utc=True).dt.floor("h"))


def scan_v2_setups(
    *,
    pair: str,
    inst_category: str,
    candles15m: pd.DataFrame,
    candles1h: pd.DataFrame,
    candles4h: pd.DataFrame,
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    universe_mask: pd.DataFrame,
    params: V2Parameters,
) -> pd.DataFrame:
    """Return causal long/short entries and the cluster-side initial stop."""
    original_index = candles15m.index
    base = add_v2_indicators(candles15m)
    base["date"] = pd.to_datetime(base["date"], utc=True)
    base = _merge_informative(base, candles1h, "1h", "signal")
    base = _merge_informative(base, candles4h, "4h", "own")
    base = _merge_informative(base, btc4h, "4h", "btc")
    base = _merge_informative(base, eth4h, "4h", "eth")
    selected_hours = _universe_hours(universe_mask, pair)
    base["in_universe"] = base["date"].dt.floor("h").isin(selected_hours)
    base["enter_long"] = 0
    base["enter_short"] = 0
    base["initial_stop_price"] = np.nan

    last_signal_candle: object = None
    pending: dict[str, float | int | str] | None = None
    zone: dict[str, float | int] | None = None
    for index, row in base.iterrows():
        signal_candle = row.get("signal_date")
        if pd.notna(signal_candle) and signal_candle != last_signal_candle:
            last_signal_candle = signal_candle
            if zone is not None:
                zone["age"] = int(zone["age"]) + 1
                if int(zone["age"]) > params.compressed_zone_max_age_1h:
                    zone = None
            if pending is None and bool(row["in_universe"]):
                compression = float(row.get("signal_compression", np.nan))
                signal_atr = float(row.get("signal_atr14", np.nan))
                cluster_high = float(row.get("signal_cluster_high", np.nan))
                cluster_low = float(row.get("signal_cluster_low", np.nan))
                signal_close = float(row.get("signal_close", np.nan))
                valid = all(
                    np.isfinite(value)
                    for value in (
                        compression,
                        signal_atr,
                        cluster_high,
                        cluster_low,
                        signal_close,
                    )
                )
                if valid and compression <= params.compression_atr:
                    zone = {
                        "cluster_high": cluster_high,
                        "cluster_low": cluster_low,
                        "age": 0,
                    }
                breakout_valid = all(
                    np.isfinite(value) for value in (signal_atr, signal_close)
                )
                if breakout_valid and zone is not None:
                    zone_high = float(zone["cluster_high"])
                    zone_low = float(zone["cluster_low"])
                    own_long = _flag(row.get("own_trend_long", False))
                    own_short = _flag(row.get("own_trend_short", False))
                    crypto = str(inst_category) == "1"
                    market_long = (not crypto) or _market_allowed(
                        row, "long", params.strict_market_consensus
                    )
                    market_short = (not crypto) or _market_allowed(
                        row, "short", params.strict_market_consensus
                    )
                    if (
                        signal_close > zone_high + params.breakout_atr * signal_atr
                        and own_long
                        and market_long
                    ):
                        pending = {
                            "side": "long",
                            "armed_index": index,
                            "wait": 0,
                            "cluster_high": zone_high,
                            "cluster_low": zone_low,
                            "signal_atr": signal_atr,
                        }
                    elif (
                        signal_close < zone_low - params.breakout_atr * signal_atr
                        and own_short
                        and market_short
                    ):
                        pending = {
                            "side": "short",
                            "armed_index": index,
                            "wait": 0,
                            "cluster_high": zone_high,
                            "cluster_low": zone_low,
                            "signal_atr": signal_atr,
                        }
                    if pending is not None:
                        zone = None

        if pending is None or index == pending["armed_index"]:
            continue
        pending["wait"] = int(pending["wait"]) + 1
        if int(pending["wait"]) > params.pullback_wait_15m:
            pending = None
            continue

        side = str(pending["side"])
        high = float(pending["cluster_high"])
        low = float(pending["cluster_low"])
        signal_atr = float(pending["signal_atr"])
        row_atr = float(row.get("atr14", signal_atr))
        if not np.isfinite(row_atr):
            row_atr = signal_atr
        if side == "long":
            invalid = float(row["low"]) <= low - params.stop_buffer_atr * signal_atr
            touched = float(row["low"]) <= high + params.pullback_atr * row_atr
            if invalid:
                pending = None
            elif touched:
                if bool(row["in_universe"]) and float(row["close"]) > high:
                    base.at[index, "enter_long"] = 1
                    base.at[index, "initial_stop_price"] = (
                        low - params.stop_buffer_atr * signal_atr
                    )
                pending = None
        else:
            invalid = float(row["high"]) >= high + params.stop_buffer_atr * signal_atr
            touched = float(row["high"]) >= low - params.pullback_atr * row_atr
            if invalid:
                pending = None
            elif touched:
                if bool(row["in_universe"]) and float(row["close"]) < low:
                    base.at[index, "enter_short"] = 1
                    base.at[index, "initial_stop_price"] = (
                        high + params.stop_buffer_atr * signal_atr
                    )
                pending = None

    base.index = original_index
    return base
