"""Causal dual moving-average pullback signal engine for OKX research."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import (
    _flag,
    _market_allowed,
    _merge_informative,
    _universe_hours,
    add_v2_indicators,
)


@dataclass(frozen=True)
class DualMaParameters:
    fast_period: int = 20
    slow_period: int = 60
    pullback_atr: float = 0.30
    pullback_wait_15m: int = 8
    stop_buffer_atr: float = 0.20
    require_4h_trend: bool = True
    strict_market_consensus: bool = False
    # Pyramid knobs live on the simulator; kept here so research grids stay together.
    pyramid_enabled: bool = True
    pyramid_trigger_r: float = 1.0
    pyramid_risk_fraction: float = 0.50
    max_pyramids: int = 1


def _ma_series(close: pd.Series, period: int, kind: str) -> pd.Series:
    if kind == "ema":
        return close.ewm(span=period, adjust=False, min_periods=period).mean()
    return close.rolling(period, min_periods=period).mean()


def _add_dual_ma_1h(frame: pd.DataFrame, params: DualMaParameters) -> pd.DataFrame:
    source = add_v2_indicators(frame)
    close = source["close"].astype(float)
    source["dual_fast"] = _ma_series(close, params.fast_period, "ema")
    source["dual_slow"] = _ma_series(close, params.slow_period, "ema")
    source["dual_long"] = source["dual_fast"] > source["dual_slow"]
    source["dual_short"] = source["dual_fast"] < source["dual_slow"]
    return source


def _informative_dual_1h(frame: pd.DataFrame, params: DualMaParameters) -> pd.DataFrame:
    source = _add_dual_ma_1h(frame, params)
    source["date"] = pd.to_datetime(source["date"], utc=True).dt.as_unit("ns")
    source["available_at"] = pd.to_datetime(source["date"], utc=True) + pd.Timedelta(hours=1)
    columns = [
        "available_at",
        "date",
        "close",
        "atr14",
        "dual_fast",
        "dual_slow",
        "dual_long",
        "dual_short",
    ]
    output = source.loc[:, columns].rename(
        columns={column: f"signal_{column}" for column in columns if column != "available_at"}
    )
    return output.sort_values("available_at")


def scan_dual_ma_setups(
    *,
    pair: str,
    inst_category: str,
    candles15m: pd.DataFrame,
    candles1h: pd.DataFrame,
    candles4h: pd.DataFrame,
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    universe_mask: pd.DataFrame,
    params: DualMaParameters,
) -> pd.DataFrame:
    """Enter on the first 15m pullback to the fast MA while 1H dual-MA trend holds."""
    if params.fast_period >= params.slow_period:
        raise ValueError("fast_period must be smaller than slow_period")
    original_index = candles15m.index
    base = add_v2_indicators(candles15m)
    base["date"] = pd.to_datetime(base["date"], utc=True).dt.as_unit("ns")
    base = pd.merge_asof(
        base.sort_values("date"),
        _informative_dual_1h(candles1h, params),
        left_on="date",
        right_on="available_at",
        direction="backward",
        allow_exact_matches=True,
    ).drop(columns="available_at")
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
    for index, row in base.iterrows():
        signal_candle = row.get("signal_date")
        if pd.notna(signal_candle) and signal_candle != last_signal_candle:
            last_signal_candle = signal_candle
            pending = None
            if not bool(row["in_universe"]):
                continue
            fast = float(row.get("signal_dual_fast", np.nan))
            slow = float(row.get("signal_dual_slow", np.nan))
            atr = float(row.get("signal_atr14", np.nan))
            dual_long = _flag(row.get("signal_dual_long", False))
            dual_short = _flag(row.get("signal_dual_short", False))
            if not all(np.isfinite(value) for value in (fast, slow, atr)) or atr <= 0:
                continue
            own_long = (not params.require_4h_trend) or _flag(row.get("own_trend_long", False))
            own_short = (not params.require_4h_trend) or _flag(row.get("own_trend_short", False))
            crypto = str(inst_category) == "1"
            market_long = (not crypto) or _market_allowed(
                row, "long", params.strict_market_consensus
            )
            market_short = (not crypto) or _market_allowed(
                row, "short", params.strict_market_consensus
            )
            if dual_long and own_long and market_long:
                pending = {
                    "side": "long",
                    "armed_index": index,
                    "wait": 0,
                    "fast": fast,
                    "slow": slow,
                    "signal_atr": atr,
                }
            elif dual_short and own_short and market_short:
                pending = {
                    "side": "short",
                    "armed_index": index,
                    "wait": 0,
                    "fast": fast,
                    "slow": slow,
                    "signal_atr": atr,
                }

        if pending is None or index == pending["armed_index"]:
            continue
        pending["wait"] = int(pending["wait"]) + 1
        if int(pending["wait"]) > params.pullback_wait_15m:
            pending = None
            continue

        side = str(pending["side"])
        fast = float(pending["fast"])
        slow = float(pending["slow"])
        signal_atr = float(pending["signal_atr"])
        row_atr = float(row.get("atr14", signal_atr))
        if not np.isfinite(row_atr) or row_atr <= 0:
            row_atr = signal_atr
        if side == "long":
            invalid = float(row["close"]) < slow - params.stop_buffer_atr * signal_atr
            touched = float(row["low"]) <= fast + params.pullback_atr * row_atr
            trend_held = float(row["close"]) > slow
            if invalid:
                pending = None
            elif touched and trend_held and bool(row["in_universe"]) and float(row["close"]) >= fast:
                base.at[index, "enter_long"] = 1
                base.at[index, "initial_stop_price"] = (
                    slow - params.stop_buffer_atr * signal_atr
                )
                pending = None
        else:
            invalid = float(row["close"]) > slow + params.stop_buffer_atr * signal_atr
            touched = float(row["high"]) >= fast - params.pullback_atr * row_atr
            trend_held = float(row["close"]) < slow
            if invalid:
                pending = None
            elif touched and trend_held and bool(row["in_universe"]) and float(row["close"]) <= fast:
                base.at[index, "enter_short"] = 1
                base.at[index, "initial_stop_price"] = (
                    slow + params.stop_buffer_atr * signal_atr
                )
                pending = None

    base.index = original_index
    return base
