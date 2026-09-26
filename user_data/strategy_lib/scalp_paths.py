"""Fast independent 5-minute labels for screening; not a portfolio backtest."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from user_data.strategy_lib.signal_path_analysis import _signed_move
from user_data.strategy_lib.v2_backtester import EntryEvent, _exit_fill

_COLUMNS = [
    "pair", "side", "date", "close_date", "entry_price", "exit_price",
    "stop_price", "target_price", "net_return", "r_multiple", "won",
    "exit_reason", "hold_minutes", "max_hold_minutes", "fees_return",
    "funding_return", "initial_risk_return", "rank", "category",
]
_STEP_NS = pd.Timedelta(minutes=5).value


def evaluate_events(
    frames: dict[str, pd.DataFrame],
    events: list[EntryEvent],
    target_r: float,
    hold_minutes: int,
    fee_rate: float = .0005,
    slippage_rate: float = .0005,
    funding_rate_per_8h: float = .0001,
) -> pd.DataFrame:
    """Label independent entries using conservative fixed-stop/fixed-target fills.

    Every event needs a complete continuous 5m window ``[date, date + hold)``.
    Stops take precedence over targets, including the entry candle. Missing
    funding is charged on held bars at UTC 00/08/16, on both sides, matching
    ``simulate_portfolio``. There is no charge on the entry candle. Returns
    use entry notional, not margin. R uses the simulator's cost-inclusive
    initial sizing risk (including an optional event sizing stop).

    Rejected events are available in ``result.attrs['skipped_events']`` and
    ``result.attrs['skipped_counts']``. Labels ignore concurrent positions,
    portfolio risk limits, liquidation sizing limits and overlapping entries;
    a chosen candidate must still pass the portfolio simulator.
    """
    if isinstance(target_r, bool) or not np.isfinite(target_r) or target_r <= 0:
        raise ValueError("target_r must be finite and positive")
    if type(hold_minutes) is not int or hold_minutes <= 0 or hold_minutes % 5:
        raise ValueError("hold_minutes must be a positive integer multiple of 5")
    if any(not np.isfinite(rate) or rate < 0 for rate in
           (fee_rate, slippage_rate, funding_rate_per_8h)) or slippage_rate >= 1:
        raise ValueError("cost rates must be finite and nonnegative; slippage must be < 1")
    count = hold_minutes // 5
    prepared = {}
    for pair, frame in frames.items():
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        source = source.sort_values("date").drop_duplicates("date", keep="last")
        dates = pd.DatetimeIndex(source["date"])
        # Timestamp.value is always ns, even when the input uses another unit.
        times = np.array([stamp.value for stamp in dates], dtype=np.int64)
        values = source[["open", "high", "low", "close"]].to_numpy(dtype=float)
        funding_mask = (dates.minute == 0) & (dates.hour % 8 == 0)
        prepared[pair] = (times, values, np.asarray(funding_mask))

    rows, skipped = [], []
    for event in events:
        date = pd.Timestamp(event.date)
        date = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")

        def reject(reason: str) -> None:
            skipped.append({"pair": event.pair, "side": event.side, "date": date,
                            "reason": reason})

        if event.side not in {"long", "short"}:
            reject("invalid_side")
            continue
        if event.pair not in prepared:
            reject("missing_pair")
            continue
        times, values, funding_mask = prepared[event.pair]
        start = int(np.searchsorted(times, date.value))
        if start == len(times) or times[start] != date.value:
            reject("missing_entry_bar")
            continue
        window_times = times[start:start + count]
        if len(window_times) != count or np.any(np.diff(window_times) != _STEP_NS):
            reject("incomplete_5m_window")
            continue
        path = values[start:start + count]
        if (not np.isfinite(path).all() or (path <= 0).any()
                or (path[:, 1] < np.maximum(path[:, 0], path[:, 3])).any()
                or (path[:, 2] > np.minimum(path[:, 0], path[:, 3])).any()):
            reject("invalid_ohlc")
            continue
        sign = 1 if event.side == "long" else -1
        entry = path[0, 0] * (1 + sign * slippage_rate)
        stop = float(event.stop_price)
        sizing_value = getattr(event, "sizing_stop_price", None)
        sizing_stop = stop if sizing_value is None else float(sizing_value)
        if not np.isfinite(stop) or stop <= 0 or sign * (entry - stop) <= 0:
            reject("invalid_stop")
            continue
        if not np.isfinite(sizing_stop) or sizing_stop <= 0 or sign * (entry - sizing_stop) <= 0:
            reject("invalid_sizing_stop")
            continue
        risk = abs(entry - stop)
        target = entry + sign * target_r * risk
        stop_hits = path[:, 2] <= stop if sign == 1 else path[:, 1] >= stop
        target_hits = path[:, 1] >= target if sign == 1 else path[:, 2] <= target
        hit_indices = np.flatnonzero(stop_hits | target_hits)
        index = int(hit_indices[0]) if len(hit_indices) else count - 1
        if stop_hits[index]:
            raw_exit = min(stop, path[index, 0]) if sign == 1 else max(stop, path[index, 0])
            reason = "initial_stop"
        elif target_hits[index]:
            raw_exit = target
            reason = f"fixed_{target_r:g}r"
        else:
            raw_exit = path[index, 3]
            reason = f"max_hold_{hold_minutes}m"
        close_date = date + pd.Timedelta(minutes=5 * index)
        if not len(hit_indices):
            close_date += pd.Timedelta(minutes=5)
        exit_price = _exit_fill(raw_exit, event.side, slippage_rate)
        fees = fee_rate * (entry + exit_price) / entry
        funding = float(np.sum(path[1:index + 1, 0]
                       * funding_mask[start + 1:start + index + 1])) * funding_rate_per_8h / entry
        net_return = _signed_move(event.side, entry, exit_price) / entry - fees - funding
        initial_risk = abs(entry - sizing_stop) / entry + 2 * fee_rate + slippage_rate
        rows.append({
            "pair": event.pair, "side": event.side, "date": date, "close_date": close_date,
            "entry_price": entry, "exit_price": exit_price, "stop_price": stop,
            "target_price": target, "net_return": net_return,
            "r_multiple": net_return / initial_risk, "won": net_return > 0,
            "exit_reason": reason, "hold_minutes": (close_date - date).total_seconds() / 60,
            "max_hold_minutes": hold_minutes, "fees_return": fees, "funding_return": funding,
            "initial_risk_return": initial_risk, "rank": event.rank, "category": event.category,
        })
    result = pd.DataFrame(rows, columns=_COLUMNS)
    result.attrs["skipped_events"] = skipped
    result.attrs["skipped_counts"] = dict(Counter(item["reason"] for item in skipped))
    return result
