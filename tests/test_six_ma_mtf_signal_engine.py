from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.six_ma_mtf_signal_engine import (
    SixMaMtfParameters,
    scan_six_ma_mtf_setups,
)


PAIR = "ALT/USDT:USDT"
SIX = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


class SixMaMtfSignalEngineTests(unittest.TestCase):
    def test_long_signal_requires_4h_arm_then_15m_compression_and_breakout(self) -> None:
        result = scan_six_ma_mtf_setups(**long_fixture())

        # The local-compression candle only prepares the setup.  The signal is
        # emitted on the following directional-break candle; the runner can
        # therefore execute it at the next 15m open.
        self.assertEqual(list(result.index[result["enter_long"] == 1]), [2])
        self.assertEqual(int(result["enter_short"].sum()), 0)
        self.assertEqual(int(result.loc[1, "enter_long"]), 0)
        self.assertLess(float(result.loc[2, "initial_stop_price"]), 102.5)

    def test_short_signal_is_symmetric(self) -> None:
        result = scan_six_ma_mtf_setups(**short_fixture())

        self.assertEqual(list(result.index[result["enter_short"] == 1]), [2])
        self.assertEqual(int(result["enter_long"].sum()), 0)
        self.assertEqual(int(result.loc[1, "enter_short"]), 0)
        self.assertGreater(float(result.loc[2, "initial_stop_price"]), 97.5)

    def test_pullback_rejection_enters_on_first_compressed_15m_touch(self) -> None:
        fixture = long_fixture()
        fixture["params"] = SixMaMtfParameters(
            compression_4h_atr=0.20,
            breakout_4h_atr=0.10,
            compression_15m_atr=0.20,
            stop_buffer_atr=0.10,
            setup_wait_15m=24,
            entry_trigger="pullback_rejection",
        )
        fixture["candles15m"].loc[1, list(SIX)] = [101.0] * 6
        fixture["candles15m"].loc[1, ["open", "high", "low", "close"]] = [
            101.1,
            101.4,
            100.8,
            101.2,
        ]

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(list(result.index[result["enter_long"] == 1]), [1])
        self.assertLess(float(result.loc[1, "initial_stop_price"]), 100.8)

    def test_failed_first_pullback_consumes_the_4h_setup(self) -> None:
        fixture = long_fixture()
        fixture["params"] = SixMaMtfParameters(
            compression_4h_atr=0.20,
            breakout_4h_atr=0.10,
            compression_15m_atr=0.20,
            stop_buffer_atr=0.10,
            setup_wait_15m=24,
            entry_trigger="pullback_rejection",
        )

        result = scan_six_ma_mtf_setups(**fixture)

        # Row 1 touches the compressed band but closes inside it.  The later
        # breakout must not revive a setup whose first pullback already failed.
        self.assertEqual(int(result["enter_long"].sum()), 0)

    def test_4h_breakout_without_prior_six_line_compression_is_ignored(self) -> None:
        fixture = long_fixture()
        # Widen the would-be 4H zone before the directional break.  The 15m
        # setup remains valid, but it must not arm without the 4H compression.
        fixture["candles4h"].loc[0, list(SIX)] = [99.0, 99.4, 99.8, 100.2, 100.6, 101.0]

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(int(result["enter_long"].sum()), 0)
        self.assertEqual(int(result["enter_short"].sum()), 0)

    def test_full_4h_trend_filter_can_be_disabled_explicitly(self) -> None:
        fixture = long_fixture(inst_category="3")
        fixture["candles4h"].loc[:, "trend_long"] = False
        fixture["params"] = SixMaMtfParameters(
            compression_4h_atr=0.20,
            breakout_4h_atr=0.10,
            compression_15m_atr=0.20,
            breakout_15m_atr=0.10,
            stop_buffer_atr=0.10,
            zone_max_age_4h=6,
            setup_wait_15m=24,
            require_full_4h_trend=False,
        )

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(list(result.index[result["enter_long"] == 1]), [2])

    def test_15m_directional_break_without_a_prior_local_compression_is_ignored(self) -> None:
        fixture = long_fixture()
        # Keep the 15m close above the 4H zone, but remove the local six-line
        # compression that should precede the second breakout.
        fixture["candles15m"].loc[1, list(SIX)] = [100.2, 100.5, 100.8, 101.2, 101.5, 101.8]

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(int(result["enter_long"].sum()), 0)
        self.assertEqual(int(result["enter_short"].sum()), 0)

    def test_crypto_long_requires_btc_and_eth_not_to_be_opposed(self) -> None:
        fixture = long_fixture(inst_category="1")
        for key in ("btc4h", "eth4h"):
            fixture[key] = with_regime(fixture[key], "short")

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(int(result["enter_long"].sum()), 0)

    def test_non_crypto_category_ignores_btc_and_eth_filter(self) -> None:
        fixture = long_fixture(inst_category="3")
        for key in ("btc4h", "eth4h"):
            fixture[key] = with_regime(fixture[key], "short")

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(list(result.index[result["enter_long"] == 1]), [2])

    def test_pair_outside_top30_universe_cannot_signal(self) -> None:
        fixture = long_fixture()
        fixture["universe_mask"].loc[:, "eligible"] = False

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(int(result["enter_long"].sum()), 0)
        self.assertEqual(int(result["enter_short"].sum()), 0)

    def test_armed_state_survives_a_4h_boundary_while_waiting_on_15m(self) -> None:
        fixture = boundary_fixture()

        result = scan_six_ma_mtf_setups(**fixture)

        # The local compressed candle is at 11:45 and the second breakout is
        # at 12:00, across a new 4H boundary.  A state machine that resets
        # armed/pending state on every 4H candle loses this signal.
        self.assertEqual(list(result.index[result["enter_long"] == 1]), [3])

    def test_setup_wait_is_counted_in_real_15m_candles_across_4h_boundaries(self) -> None:
        fixture = boundary_fixture()
        fixture["params"] = SixMaMtfParameters(setup_wait_15m=1)
        # Keep the local compression at 11:45, but delay the directional
        # break by two actual 15m candles.  Crossing 12:00 must not reset the
        # counter or grant a fresh wait window.
        fixture["candles15m"].loc[3, list(SIX)] = [100.2, 100.5, 100.8, 101.2, 101.5, 101.8]
        fixture["candles15m"].loc[3, ["open", "high", "low", "close"]] = [101.1, 101.4, 100.9, 101.2]
        fixture["candles15m"].loc[4, ["open", "high", "low", "close"]] = [101.2, 101.5, 101.0, 101.3]
        fixture["candles15m"].loc[5, ["open", "high", "low", "close"]] = [101.3, 102.7, 101.2, 102.5]

        result = scan_six_ma_mtf_setups(**fixture)

        self.assertEqual(int(result["enter_long"].sum()), 0)

    def test_appending_future_candles_does_not_change_historical_signals(self) -> None:
        fixture = long_fixture()
        historical = scan_six_ma_mtf_setups(**fixture)
        extended = {**fixture, "candles15m": append_future(fixture["candles15m"])}
        extended_result = scan_six_ma_mtf_setups(**extended)

        pd.testing.assert_frame_equal(
            historical[["enter_long", "enter_short", "initial_stop_price"]],
            extended_result.loc[
                historical.index,
                ["enter_long", "enter_short", "initial_stop_price"],
            ],
        )


def _ohlc_row(
    date: pd.Timestamp,
    close: float,
    averages: list[float],
    *,
    high: float | None = None,
    low: float | None = None,
    atr14: float = 1.0,
    trend_long: bool = False,
    trend_short: bool = False,
) -> dict[str, object]:
    if high is None:
        high = close + 0.2
    if low is None:
        low = close - 0.2
    row: dict[str, object] = {
        "date": date,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1_000.0,
        "quote_volume": 1_000_000.0,
        "atr14": atr14,
        "trend_long": trend_long,
        "trend_short": trend_short,
    }
    row.update(dict(zip(SIX, averages)))
    return row


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _params(*, setup_wait_15m: int = 24) -> SixMaMtfParameters:
    return SixMaMtfParameters(
        compression_4h_atr=0.20,
        breakout_4h_atr=0.10,
        compression_15m_atr=0.20,
        breakout_15m_atr=0.10,
        stop_buffer_atr=0.10,
        zone_max_age_4h=6,
        setup_wait_15m=setup_wait_15m,
    )


def four_hour_fixture(side: str = "long") -> pd.DataFrame:
    dates = pd.date_range("2025-01-01 00:00", periods=4, freq="4h", tz="UTC")
    if side == "long":
        closes = [100.0, 102.0, 102.5, 103.0]
        # Keep EMA20 > EMA60 > EMA120 so an implementation that derives the
        # 4H trend from the supplied averages sees the same long regime.
        break_averages = [99.8, 100.0, 100.2, 100.3, 100.1, 99.9]
        trend_long, trend_short = True, False
    else:
        closes = [100.0, 98.0, 97.5, 97.0]
        # Mirror the long values with EMA20 < EMA60 < EMA120.
        break_averages = [100.2, 100.0, 99.8, 99.7, 99.9, 100.1]
        trend_long, trend_short = False, True
    rows = [
        _ohlc_row(dates[0], closes[0], [100.0] * 6),
        _ohlc_row(
            dates[1],
            closes[1],
            break_averages,
            trend_long=trend_long,
            trend_short=trend_short,
        ),
        _ohlc_row(
            dates[2],
            closes[2],
            break_averages,
            trend_long=trend_long,
            trend_short=trend_short,
        ),
        _ohlc_row(
            dates[3],
            closes[3],
            break_averages,
            trend_long=trend_long,
            trend_short=trend_short,
        ),
    ]
    return _frame(rows)


def fifteen_minute_fixture(side: str = "long") -> pd.DataFrame:
    dates = pd.date_range("2025-01-01 08:00", periods=8, freq="15min", tz="UTC")
    if side == "long":
        closes = [102.0, 101.0, 102.5, 102.6, 102.7, 102.8, 102.9, 103.0]
        averages = [
            [100.6, 100.8, 101.0, 101.2, 101.4, 101.6],
            [101.0] * 6,
            [100.6, 100.8, 101.0, 101.2, 101.4, 101.6],
        ]
        ohlc = [
            (102.2, 101.8),
            (101.2, 100.4),
            (102.8, 101.8),
        ]
    else:
        closes = [98.0, 99.0, 97.5, 97.4, 97.3, 97.2, 97.1, 97.0]
        averages = [
            [98.4, 98.6, 98.8, 99.0, 99.2, 99.4],
            [99.0] * 6,
            [98.4, 98.6, 98.8, 99.0, 99.2, 99.4],
        ]
        ohlc = [
            (98.2, 97.8),
            (99.6, 98.4),
            (98.2, 97.2),
        ]

    rows: list[dict[str, object]] = []
    for index, date in enumerate(dates):
        average_values = averages[index] if index < 3 else averages[2]
        high, low = ohlc[index] if index < 3 else (closes[index] + 0.2, closes[index] - 0.2)
        rows.append(
            _ohlc_row(
                date,
                closes[index],
                average_values,
                high=high,
                low=low,
            )
        )
    return _frame(rows)


def reference_4h(side: str) -> pd.DataFrame:
    return four_hour_fixture(side)


def universe_mask(start: str = "2025-01-01 08:00", periods: int = 8) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=periods, freq="h", tz="UTC"),
            "pair": PAIR,
            "rank": 1,
            "score": 1.0,
            "eligible": True,
        }
    )


def long_fixture(*, inst_category: str = "1") -> dict[str, object]:
    return {
        "pair": PAIR,
        "inst_category": inst_category,
        "candles15m": fifteen_minute_fixture("long"),
        "candles4h": four_hour_fixture("long"),
        "btc4h": reference_4h("long"),
        "eth4h": reference_4h("long"),
        "universe_mask": universe_mask(),
        "params": _params(),
    }


def short_fixture() -> dict[str, object]:
    return {
        "pair": PAIR,
        "inst_category": "1",
        "candles15m": fifteen_minute_fixture("short"),
        "candles4h": four_hour_fixture("short"),
        "btc4h": reference_4h("short"),
        "eth4h": reference_4h("short"),
        "universe_mask": universe_mask(),
        "params": _params(),
    }


def with_regime(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    output = frame.copy()
    output["trend_long"] = side == "long"
    output["trend_short"] = side == "short"
    return output


def boundary_fixture() -> dict[str, object]:
    dates = pd.date_range("2025-01-01 11:15", periods=6, freq="15min", tz="UTC")
    rows = [
        _ohlc_row(dates[0], 102.0, [100.6, 100.8, 101.0, 101.2, 101.4, 101.6]),
        _ohlc_row(dates[1], 101.0, [101.0] * 6, high=101.2, low=100.4),
        _ohlc_row(dates[2], 101.1, [101.0] * 6, high=101.3, low=100.7),
        _ohlc_row(dates[3], 102.5, [100.6, 100.8, 101.0, 101.2, 101.4, 101.6], high=102.8, low=101.8),
        _ohlc_row(dates[4], 102.6, [100.6, 100.8, 101.0, 101.2, 101.4, 101.6]),
        _ohlc_row(dates[5], 102.7, [100.6, 100.8, 101.0, 101.2, 101.4, 101.6], high=103.0, low=102.0),
    ]
    candles15m = _frame(rows)
    candles4h = four_hour_fixture("long")
    return {
        "pair": PAIR,
        "inst_category": "3",
        "candles15m": candles15m,
        "candles4h": candles4h,
        "btc4h": reference_4h("short"),
        "eth4h": reference_4h("short"),
        "universe_mask": universe_mask("2025-01-01 11:00", periods=3),
        # The local setup is deliberately placed just before the next 4H
        # boundary.  Give it a longer explicit window so the test exercises
        # state continuity rather than the ordinary timeout threshold.
        "params": _params(setup_wait_15m=48),
    }


def append_future(frame: pd.DataFrame) -> pd.DataFrame:
    future = frame.iloc[-1:].copy()
    future["date"] = future["date"] + pd.Timedelta(minutes=15)
    future[["open", "high", "low", "close"]] = [60.0, 140.0, 50.0, 130.0]
    return pd.concat([frame, future], ignore_index=True)


if __name__ == "__main__":
    unittest.main()
