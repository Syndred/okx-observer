"""Path diagnostics that separate signal direction from portfolio execution."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent


def _signed_move(side: str, entry: float, price: float) -> float:
    return price - entry if side == "long" else entry - price


def _window_complete(
    frame: pd.DataFrame, entry_date: pd.Timestamp, hours: int
) -> bool:
    if frame.empty:
        return False
    expected = pd.date_range(
        entry_date,
        periods=hours * 4,
        freq="15min",
        tz=entry_date.tz,
    )
    actual = pd.DatetimeIndex(frame["date"])
    return len(actual) >= len(expected) and actual[: len(expected)].equals(expected)


def analyze_signal_paths(
    base_frames: dict[str, pd.DataFrame],
    events: Iterable[EntryEvent],
    horizons_hours: tuple[int, ...] = (1, 3, 6, 12, 24),
    target_rs: tuple[int, ...] = (1, 2, 3, 5),
    analysis_hours: int = 24,
) -> pd.DataFrame:
    """Measure causal forward MFE/MAE and target-before-stop for raw signals.

    The entry is the open of ``event.date``.  Each horizon uses only candles
    whose open time is within ``[entry, entry + horizon)``.  If a candle hits
    both the original stop and a target, the stop wins conservatively.
    Invalid stops and events without an exact entry candle are omitted.
    """
    if analysis_hours <= 0 or not horizons_hours or not target_rs:
        raise ValueError("analysis horizon and target levels must be positive")
    if min(horizons_hours) <= 0 or min(target_rs) <= 0:
        raise ValueError("analysis horizon and target levels must be positive")

    normalized: dict[str, pd.DataFrame] = {}
    for pair, frame in base_frames.items():
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        normalized[pair] = source.sort_values("date").drop_duplicates(
            "date", keep="last"
        ).reset_index(drop=True)

    rows: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: (item.date, item.pair, item.side)):
        frame = normalized.get(event.pair)
        if frame is None or frame.empty or event.side not in {"long", "short"}:
            continue
        entry_date = pd.Timestamp(event.date)
        if entry_date.tzinfo is None:
            entry_date = entry_date.tz_localize("UTC")
        else:
            entry_date = entry_date.tz_convert("UTC")
        entry_match = frame.index[frame["date"] == entry_date]
        if entry_match.empty:
            continue
        entry_index = int(entry_match[0])
        entry_price = float(frame.at[entry_index, "open"])
        stop_price = float(event.stop_price)
        correctly_sided = (
            stop_price < entry_price
            if event.side == "long"
            else stop_price > entry_price
        )
        risk_price = abs(entry_price - stop_price)
        if (
            not correctly_sided
            or not np.isfinite(entry_price)
            or not np.isfinite(stop_price)
            or risk_price <= 0
        ):
            continue

        analysis_end = entry_date + pd.Timedelta(hours=analysis_hours)
        path = frame.loc[
            (frame["date"] >= entry_date) & (frame["date"] < analysis_end)
        ].copy()
        if path.empty:
            continue
        complete_window = _window_complete(path, entry_date, analysis_hours)
        row: dict[str, object] = {
            "pair": event.pair,
            "side": event.side,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "risk_price": risk_price,
            "rank": event.rank,
            "category": event.category,
            "complete_window": complete_window,
        }

        for hours in horizons_hours:
            horizon_end = entry_date + pd.Timedelta(hours=hours)
            selected = path.loc[path["date"] < horizon_end]
            complete = _window_complete(selected, entry_date, hours)
            if not complete:
                row[f"return_{hours}h_r"] = np.nan
                row[f"mfe_{hours}h_r"] = np.nan
                row[f"mae_{hours}h_r"] = np.nan
                continue
            final_close = float(selected["close"].iloc[-1])
            if event.side == "long":
                favorable_price = float(selected["high"].max())
                adverse_price = float(selected["low"].min())
                mfe = favorable_price - entry_price
                mae = entry_price - adverse_price
            else:
                favorable_price = float(selected["low"].min())
                adverse_price = float(selected["high"].max())
                mfe = entry_price - favorable_price
                mae = adverse_price - entry_price
            row[f"return_{hours}h_r"] = (
                _signed_move(event.side, entry_price, final_close) / risk_price
            )
            row[f"mfe_{hours}h_r"] = max(0.0, mfe / risk_price)
            row[f"mae_{hours}h_r"] = max(0.0, mae / risk_price)

        target_results = {target_r: False for target_r in target_rs}
        stop_time: pd.Timestamp | None = None
        for candle in path.itertuples(index=False):
            candle_time = pd.Timestamp(candle.date)
            stop_hit = (
                float(candle.low) <= stop_price
                if event.side == "long"
                else float(candle.high) >= stop_price
            )
            if stop_hit:
                stop_time = candle_time
                break
            for target_r in target_rs:
                target_price = entry_price + (
                    target_r * risk_price
                    if event.side == "long"
                    else -target_r * risk_price
                )
                target_hit = (
                    float(candle.high) >= target_price
                    if event.side == "long"
                    else float(candle.low) <= target_price
                )
                if target_hit:
                    target_results[target_r] = True

        row["stop_hit"] = stop_time is not None
        row["stop_time"] = stop_time if stop_time is not None else pd.NaT
        for target_r, reached in target_results.items():
            row[f"target_{target_r}r_before_stop"] = reached

        after_stop = (
            path.loc[path["date"] > stop_time]
            if stop_time is not None
            else path.iloc[0:0]
        )
        for target_r in (3, 5):
            if after_stop.empty:
                reached_after_stop = False
            elif event.side == "long":
                reached_after_stop = bool(
                    after_stop["high"].max()
                    >= entry_price + target_r * risk_price
                )
            else:
                reached_after_stop = bool(
                    after_stop["low"].min()
                    <= entry_price - target_r * risk_price
                )
            row[f"stopped_then_{target_r}r"] = reached_after_stop
        rows.append(row)

    return pd.DataFrame(rows)
