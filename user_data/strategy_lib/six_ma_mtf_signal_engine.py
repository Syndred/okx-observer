"""Causal 4H/15m nested six-moving-average compression breakout signals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import (
    SIX_AVERAGES,
    _flag,
    _market_allowed,
    _merge_informative,
    _universe_hours,
    add_v2_indicators,
)


@dataclass(frozen=True)
class SixMaMtfParameters:
    compression_4h_atr: float = 1.20
    breakout_4h_atr: float = 0.10
    compression_15m_atr: float = 1.40
    breakout_15m_atr: float = 0.10
    stop_buffer_atr: float = 0.20
    zone_max_age_4h: int = 6
    setup_wait_15m: int = 24
    entry_trigger: str = "nested_breakout"
    pullback_touch_atr: float = 0.20
    pullback_reject_atr: float = 0.00
    require_full_4h_trend: bool = True
    strict_market_consensus: bool = False


def _add_cluster(frame: pd.DataFrame) -> pd.DataFrame:
    source = add_v2_indicators(frame)
    source["cluster_high"] = source.loc[:, SIX_AVERAGES].max(axis=1)
    source["cluster_low"] = source.loc[:, SIX_AVERAGES].min(axis=1)
    source["compression"] = (
        source["cluster_high"] - source["cluster_low"]
    ) / source["atr14"]
    return source


def _informative_4h(
    frame: pd.DataFrame, params: SixMaMtfParameters
) -> pd.DataFrame:
    source = _add_cluster(frame)
    source["breakout_long"] = False
    source["breakout_short"] = False
    higher_zone: dict[str, float | int] | None = None
    for index, row in source.iterrows():
        if higher_zone is not None:
            higher_zone["age"] = int(higher_zone["age"]) + 1
            if int(higher_zone["age"]) > params.zone_max_age_4h:
                higher_zone = None
        atr = float(row.get("atr14", np.nan))
        close = float(row.get("close", np.nan))
        compression = float(row.get("compression", np.nan))
        cluster_high = float(row.get("cluster_high", np.nan))
        cluster_low = float(row.get("cluster_low", np.nan))
        valid = _finite(
            atr, close, compression, cluster_high, cluster_low
        ) and atr > 0
        breakout = False
        if valid and higher_zone is not None:
            long_regime = (
                not params.require_full_4h_trend
                or _flag(row.get("trend_long", False))
            )
            short_regime = (
                not params.require_full_4h_trend
                or _flag(row.get("trend_short", False))
            )
            if (
                long_regime
                and close
                > float(higher_zone["high"]) + params.breakout_4h_atr * atr
            ):
                source.at[index, "breakout_long"] = True
                breakout = True
            elif (
                short_regime
                and close
                < float(higher_zone["low"]) - params.breakout_4h_atr * atr
            ):
                source.at[index, "breakout_short"] = True
                breakout = True
            if breakout:
                higher_zone = None
        if (
            valid
            and not breakout
            and compression <= params.compression_4h_atr
        ):
            higher_zone = {"high": cluster_high, "low": cluster_low, "age": 0}
    source["date"] = pd.to_datetime(source["date"], utc=True).dt.as_unit("ns")
    source["available_at"] = source["date"] + pd.Timedelta(hours=4)
    columns = [
        "available_at",
        "date",
        "close",
        "atr14",
        "cluster_high",
        "cluster_low",
        "compression",
        "trend_long",
        "trend_short",
        "breakout_long",
        "breakout_short",
    ]
    return source.loc[:, columns].rename(
        columns={
            column: f"signal_{column}"
            for column in columns
            if column != "available_at"
        }
    ).sort_values("available_at")


def _finite(*values: float) -> bool:
    return all(np.isfinite(value) for value in values)


def scan_six_ma_mtf_setups(
    *,
    pair: str,
    inst_category: str,
    candles15m: pd.DataFrame,
    candles4h: pd.DataFrame,
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    universe_mask: pd.DataFrame,
    params: SixMaMtfParameters,
) -> pd.DataFrame:
    """Find 4H six-MA breakouts with a causal 15m six-MA entry trigger."""
    if min(params.zone_max_age_4h, params.setup_wait_15m) <= 0:
        raise ValueError("zone and setup lifetimes must be positive")
    if params.entry_trigger not in {
        "compression_close",
        "nested_breakout",
        "pullback_rejection",
    }:
        raise ValueError(f"unsupported entry_trigger: {params.entry_trigger}")
    original_index = candles15m.index
    base = _add_cluster(candles15m)
    base["date"] = pd.to_datetime(base["date"], utc=True).dt.as_unit("ns")
    base = pd.merge_asof(
        base.sort_values("date"),
        _informative_4h(candles4h, params),
        left_on="date",
        right_on="available_at",
        direction="backward",
        allow_exact_matches=True,
    ).drop(columns="available_at")
    base = _merge_informative(base, btc4h, "4h", "btc")
    base = _merge_informative(base, eth4h, "4h", "eth")
    selected_hours = _universe_hours(universe_mask, pair)
    base["in_universe"] = base["date"].dt.floor("h").isin(selected_hours)
    base["enter_long"] = 0
    base["enter_short"] = 0
    base["initial_stop_price"] = np.nan

    last_4h_candle: object = None
    setup: dict[str, object] | None = None
    local_zone: dict[str, object] | None = None

    for index, row in base.iterrows():
        signal_candle = row.get("signal_date")
        new_4h_candle = pd.notna(signal_candle) and signal_candle != last_4h_candle
        if new_4h_candle:
            last_4h_candle = signal_candle
            own_long = _flag(row.get("signal_trend_long", False))
            own_short = _flag(row.get("signal_trend_short", False))
            if setup is not None:
                still_valid = (
                    not params.require_full_4h_trend
                    or (own_long if setup["side"] == "long" else own_short)
                )
                if not still_valid:
                    setup = None
                    local_zone = None

            if setup is None:
                crypto = str(inst_category) == "1"
                market_long = (not crypto) or _market_allowed(
                    row, "long", params.strict_market_consensus
                )
                market_short = (not crypto) or _market_allowed(
                    row, "short", params.strict_market_consensus
                )
                if (
                    _flag(row.get("signal_breakout_long", False))
                    and (not params.require_full_4h_trend or own_long)
                    and market_long
                ):
                    setup = {
                        "side": "long",
                        "armed_at": pd.Timestamp(row["date"]),
                        "armed_index": index,
                    }
                    local_zone = None
                elif (
                    _flag(row.get("signal_breakout_short", False))
                    and (not params.require_full_4h_trend or own_short)
                    and market_short
                ):
                    setup = {
                        "side": "short",
                        "armed_at": pd.Timestamp(row["date"]),
                        "armed_index": index,
                    }
                    local_zone = None

        if setup is None or index == setup["armed_index"]:
            continue
        elapsed = pd.Timestamp(row["date"]) - pd.Timestamp(setup["armed_at"])
        if elapsed > pd.Timedelta(minutes=15 * params.setup_wait_15m):
            setup = None
            local_zone = None
            continue

        side = str(setup["side"])
        local_atr = float(row.get("atr14", np.nan))
        local_compression = float(row.get("compression", np.nan))
        local_high = float(row.get("cluster_high", np.nan))
        local_low = float(row.get("cluster_low", np.nan))
        local_valid = _finite(
            local_atr,
            local_compression,
            local_high,
            local_low,
        ) and local_atr > 0

        if params.entry_trigger == "compression_close":
            if not local_valid or local_compression > params.compression_15m_atr:
                continue
            if bool(row["in_universe"]):
                if side == "long":
                    base.at[index, "enter_long"] = 1
                    base.at[index, "initial_stop_price"] = (
                        local_low - params.stop_buffer_atr * local_atr
                    )
                else:
                    base.at[index, "enter_short"] = 1
                    base.at[index, "initial_stop_price"] = (
                        local_high + params.stop_buffer_atr * local_atr
                    )
            setup = None
            continue

        if params.entry_trigger == "pullback_rejection":
            if not local_valid or local_compression > params.compression_15m_atr:
                continue
            close = float(row["close"])
            if side == "long":
                invalid = close < local_low - params.stop_buffer_atr * local_atr
                touched = (
                    float(row["low"])
                    <= local_high + params.pullback_touch_atr * local_atr
                )
                if invalid:
                    setup = None
                elif touched:
                    if (
                        bool(row["in_universe"])
                        and close
                        > local_high + params.pullback_reject_atr * local_atr
                    ):
                        base.at[index, "enter_long"] = 1
                        base.at[index, "initial_stop_price"] = (
                            min(float(row["low"]), local_low)
                            - params.stop_buffer_atr * local_atr
                        )
                    setup = None
            else:
                invalid = close > local_high + params.stop_buffer_atr * local_atr
                touched = (
                    float(row["high"])
                    >= local_low - params.pullback_touch_atr * local_atr
                )
                if invalid:
                    setup = None
                elif touched:
                    if (
                        bool(row["in_universe"])
                        and close
                        < local_low - params.pullback_reject_atr * local_atr
                    ):
                        base.at[index, "enter_short"] = 1
                        base.at[index, "initial_stop_price"] = (
                            max(float(row["high"]), local_high)
                            + params.stop_buffer_atr * local_atr
                        )
                    setup = None
            continue

        if local_zone is None:
            if local_valid and local_compression <= params.compression_15m_atr:
                local_zone = {
                    "high": local_high,
                    "low": local_low,
                    "atr": local_atr,
                    "formed_index": index,
                }
            continue
        if index == local_zone["formed_index"]:
            continue

        zone_high = float(local_zone["high"])
        zone_low = float(local_zone["low"])
        zone_atr = float(local_zone["atr"])
        close = float(row["close"])
        in_universe = bool(row["in_universe"])
        if side == "long":
            if close < zone_low - params.stop_buffer_atr * zone_atr:
                setup = None
                local_zone = None
            elif (
                in_universe
                and close > zone_high + params.breakout_15m_atr * zone_atr
            ):
                base.at[index, "enter_long"] = 1
                base.at[index, "initial_stop_price"] = (
                    zone_low - params.stop_buffer_atr * zone_atr
                )
                setup = None
                local_zone = None
        else:
            if close > zone_high + params.stop_buffer_atr * zone_atr:
                setup = None
                local_zone = None
            elif (
                in_universe
                and close < zone_low - params.breakout_15m_atr * zone_atr
            ):
                base.at[index, "enter_short"] = 1
                base.at[index, "initial_stop_price"] = (
                    zone_high + params.stop_buffer_atr * zone_atr
                )
                setup = None
                local_zone = None

    base.index = original_index
    return base
