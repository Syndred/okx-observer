from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.run_trend_compression_rolling_research import build_pair_events
from user_data.strategy_lib.trend_compression_history import (
    TrendCompressionParameters,
    scan_trend_compression_setups,
)


UTC = "UTC"
PAIR = "ALT-USDT-SWAP"
SIX = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


def _row(
    date: pd.Timestamp,
    close: float,
    averages: list[float],
    *,
    atr: float = 1.0,
    high: float | None = None,
    low: float | None = None,
) -> dict[str, object]:
    if high is None:
        high = close + 0.2
    if low is None:
        low = close - 0.2
    return {
        "date": date,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1_000.0,
        "quote_volume": 1_000_000.0,
        "atr14": atr,
        **dict(zip(SIX, averages)),
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _trend_frame(side: str, date: str) -> pd.DataFrame:
    timestamp = pd.Timestamp(date, tz=UTC)
    if side == "long":
        values = {
            "close": 110.0,
            "ema20": 105.0,
            "ema60": 103.0,
            "ema60_slope3": 1.0,
        }
    elif side == "short":
        values = {
            "close": 90.0,
            "ema20": 95.0,
            "ema60": 97.0,
            "ema60_slope3": -1.0,
        }
    else:
        raise ValueError(side)
    # The trend contract only consumes close/EMA20/EMA60/slope3.  Supplying
    # the remaining lines keeps this fixture explicit and prevents any
    # indicator warm-up from becoming an accidental test dependency.
    if side == "long":
        averages = [104.0, 102.0, 100.0, 105.0, 103.0, 101.0]
    else:
        averages = [96.0, 98.0, 100.0, 95.0, 97.0, 99.0]
    return pd.DataFrame(
        [
            {
                **_row(timestamp, values["close"], averages),
                "ema20": values["ema20"],
                "ema60": values["ema60"],
                "ema60_slope3": values["ema60_slope3"],
            }
        ]
    )


def _compression_frame(
    side: str = "long",
    *,
    dates: list[pd.Timestamp] | None = None,
    spreads: list[float] | None = None,
    atr: float = 1.0,
) -> pd.DataFrame:
    dates = dates or list(
        pd.date_range("2025-01-02 00:00", periods=3, freq="15min", tz=UTC)
    )
    spreads = spreads or [3.0, 1.5, 1.5]
    if len(dates) != len(spreads):
        raise ValueError("dates and spreads must have the same length")
    rows: list[dict[str, object]] = []
    for date, spread in zip(dates, spreads):
        if side == "long":
            low = 100.0
            averages = np.linspace(low, low + spread, len(SIX)).tolist()
            close = 105.0
        elif side == "short":
            high = 100.0
            averages = np.linspace(high, high - spread, len(SIX)).tolist()
            close = 95.0
        else:
            raise ValueError(side)
        rows.append(_row(date, close, averages, atr=atr))
    return _frame(rows)


def _universe(dates: list[pd.Timestamp]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "pair": PAIR,
            "rank": 1,
            "score": 1.0,
            "eligible": True,
        }
    )


def _scan(
    candles15m: pd.DataFrame,
    *,
    side: str = "long",
    daily_date: str = "2025-01-01 00:00",
    four_hour_date: str = "2025-01-01 16:00",
    universe_mask: pd.DataFrame | None = None,
    params: TrendCompressionParameters | None = None,
    daily_frame: pd.DataFrame | None = None,
    four_hour_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    daily = daily_frame if daily_frame is not None else _trend_frame(side, daily_date)
    four_hour = (
        four_hour_frame
        if four_hour_frame is not None
        else _trend_frame(side, four_hour_date)
    )
    universe_mask = universe_mask if universe_mask is not None else _universe(
        list(pd.to_datetime(candles15m["date"], utc=True).dt.floor("h").unique())
    )
    return scan_trend_compression_setups(
        pair=PAIR,
        candles15m=candles15m,
        candles4h=four_hour,
        candles1d=daily,
        universe_mask=universe_mask,
        params=params or TrendCompressionParameters(compression_15m_atr=2.0, stop_buffer_atr=0.2),
    )


class TrendCompressionHistoryTests(unittest.TestCase):
    def test_signal_waits_for_closed_daily_and_four_hour_trends_then_emits_on_first_compression(
        self,
    ) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=3, freq="15min", tz=UTC)
        )
        signals = _scan(_compression_frame(dates=dates, spreads=[3.0, 1.5, 1.5]))

        self.assertEqual(list(signals.index[signals["enter_long"] == 1]), [1])
        self.assertEqual(int(signals["enter_short"].sum()), 0)
        self.assertAlmostEqual(float(signals.loc[1, "initial_stop_price"]), 99.8)
        # The signal candle is not the execution candle: the runner must use
        # the following 15m open.
        self.assertEqual(
            signals.loc[1, "date"] + pd.Timedelta(minutes=15),
            signals.loc[2, "date"],
        )

    def test_runner_executes_signal_on_the_following_15m_open(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=3, freq="15min", tz=UTC)
        )
        candles15m = _compression_frame(dates=dates, spreads=[3.0, 1.5, 1.5])
        events = build_pair_events(
            PAIR,
            "1",
            candles15m,
            _trend_frame("long", "2025-01-01 16:00"),
            _trend_frame("long", "2025-01-01 00:00"),
            TrendCompressionParameters(compression_15m_atr=2.0, stop_buffer_atr=0.2),
        )

        self.assertEqual(len(events), 1)
        entry_date, pair, side, stop_price, category, _ = events[0]
        self.assertEqual(entry_date, dates[2])
        self.assertEqual(pair, PAIR)
        self.assertEqual(side, "long")
        self.assertEqual(category, "1")
        self.assertAlmostEqual(stop_price, 99.8)

    def test_daily_bar_is_not_used_before_its_close(self) -> None:
        dates = list(
            pd.date_range("2025-01-01 23:45", periods=2, freq="15min", tz=UTC)
        )
        compressed = _compression_frame(dates=dates, spreads=[1.5, 1.5])
        signals = _scan(compressed)

        # D1 starts at 00:00 and is only available at the next UTC midnight.
        self.assertEqual(int(signals.loc[0, "enter_long"]), 0)
        self.assertEqual(int(signals.loc[1, "enter_long"]), 1)

    def test_each_basic_trend_clause_and_four_hour_alignment_is_required(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=2, freq="15min", tz=UTC)
        )
        compressed = _compression_frame(dates=dates, spreads=[3.0, 1.5])
        for column, value in (
            ("close", 104.0),
            ("ema20", 102.0),
            ("ema60_slope3", -1.0),
        ):
            daily = _trend_frame("long", "2025-01-01 00:00")
            daily.loc[0, column] = value
            with self.subTest(column=column):
                signals = _scan(compressed, daily_frame=daily)
                self.assertEqual(int(signals["enter_long"].sum()), 0)

        signals = _scan(
            compressed,
            four_hour_frame=_trend_frame("short", "2025-01-01 16:00"),
        )
        self.assertEqual(int(signals["enter_long"].sum()), 0)
        self.assertEqual(int(signals["enter_short"].sum()), 0)

    def test_compression_episode_emits_once_and_rearms_after_leaving_density(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=5, freq="15min", tz=UTC)
        )
        signals = _scan(
            _compression_frame(dates=dates, spreads=[3.0, 1.5, 1.5, 3.0, 1.5])
        )

        self.assertEqual(list(signals.index[signals["enter_long"] == 1]), [1, 4])
        self.assertEqual(int(signals["enter_long"].sum()), 2)

    def test_short_signal_and_stop_are_mirror_images(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=3, freq="15min", tz=UTC)
        )
        signals = _scan(
            _compression_frame("short", dates=dates, spreads=[3.0, 1.5, 1.5]),
            side="short",
        )

        self.assertEqual(list(signals.index[signals["enter_short"] == 1]), [1])
        self.assertEqual(int(signals["enter_long"].sum()), 0)
        self.assertAlmostEqual(float(signals.loc[1, "initial_stop_price"]), 100.2)

    def test_threshold_and_universe_gate_are_strictly_applied(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=2, freq="15min", tz=UTC)
        )
        at_threshold = _scan(
            _compression_frame(dates=dates, spreads=[3.0, 2.0]),
            params=TrendCompressionParameters(compression_15m_atr=2.0),
        )
        self.assertEqual(list(at_threshold.index[at_threshold["enter_long"] == 1]), [1])

        outside = _universe(dates)
        outside["eligible"] = False
        no_universe_signal = _scan(
            _compression_frame(dates=dates, spreads=[3.0, 1.5]),
            universe_mask=outside,
        )
        self.assertEqual(int(no_universe_signal["enter_long"].sum()), 0)

    def test_appending_future_rows_does_not_rewrite_historical_signals(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=3, freq="15min", tz=UTC)
        )
        historical = _compression_frame(dates=dates, spreads=[3.0, 1.5, 1.5])
        before = _scan(historical)
        future_dates = list(
            pd.date_range("2025-01-02 00:45", periods=2, freq="15min", tz=UTC)
        )
        future = _compression_frame("short", dates=future_dates, spreads=[3.0, 3.0])
        daily = _trend_frame("long", "2025-01-01 00:00")
        four_hour = _trend_frame("long", "2025-01-01 16:00")
        future_daily = _trend_frame("short", "2025-01-03 00:00")
        future_four_hour = _trend_frame("short", "2025-01-02 08:00")
        after = _scan(
            pd.concat([historical, future], ignore_index=True),
            daily_frame=pd.concat([daily, future_daily], ignore_index=True),
            four_hour_frame=pd.concat([four_hour, future_four_hour], ignore_index=True),
        )

        columns = ["date", "enter_long", "enter_short", "initial_stop_price"]
        pd.testing.assert_frame_equal(
            before[columns].reset_index(drop=True),
            after.loc[before.index, columns].reset_index(drop=True),
        )

    def test_invalid_parameters_are_rejected(self) -> None:
        dates = list(
            pd.date_range("2025-01-02 00:00", periods=2, freq="15min", tz=UTC)
        )
        with self.assertRaises(ValueError):
            _scan(
                _compression_frame(dates=dates),
                params=TrendCompressionParameters(compression_15m_atr=0),
            )
        with self.assertRaises(ValueError):
            _scan(
                _compression_frame(dates=dates),
                params=TrendCompressionParameters(stop_buffer_atr=-0.1),
            )


if __name__ == "__main__":
    unittest.main()
