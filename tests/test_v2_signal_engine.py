from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.v2_signal_engine import V2Parameters, scan_v2_setups


PAIR = "ALT/USDT:USDT"
SIX = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


class V2SignalEngineTests(unittest.TestCase):
    def test_crypto_long_requires_own_trend_and_non_opposing_market(self) -> None:
        result = scan_v2_setups(**long_fixture(inst_category="1", eth_regime="neutral"))

        self.assertEqual(result["enter_long"].sum(), 1)
        self.assertEqual(result["enter_short"].sum(), 0)

        opposed = scan_v2_setups(**long_fixture(inst_category="1", eth_regime="short"))
        self.assertEqual(opposed["enter_long"].sum(), 0)

    def test_equity_contract_ignores_btc_eth_regime(self) -> None:
        result = scan_v2_setups(
            **long_fixture(inst_category="3", btc_regime="short", eth_regime="short")
        )

        self.assertEqual(result["enter_long"].sum(), 1)

    def test_breakout_arm_row_cannot_also_be_pullback(self) -> None:
        fixture = long_fixture(inst_category="1")
        fixture["candles15m"].loc[0, ["low", "close"]] = [99.99, 100.2]
        fixture["candles15m"].loc[1:, "low"] = 100.6

        result = scan_v2_setups(**fixture)

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_breakout_can_use_a_prior_compressed_zone(self) -> None:
        fixture = long_fixture(inst_category="1")
        signal = fixture["candles1h"].copy()
        signal.loc[1, "close"] = 100.0
        expanded = signal.iloc[-1:].copy()
        expanded["date"] = expanded["date"] + pd.Timedelta(hours=1)
        expanded["close"] = 101.0
        expanded["ma20"] = 99.0
        expanded["ema20"] = 101.0
        fixture["candles1h"] = pd.concat([signal, expanded], ignore_index=True)
        fixture["candles15m"].loc[:4, "low"] = 100.6
        fixture["candles15m"].loc[5, ["low", "close"]] = [99.99, 100.2]

        result = scan_v2_setups(**fixture)

        self.assertEqual(list(result.index[result["enter_long"] == 1]), [5])

    def test_first_pullback_enters_and_later_pullbacks_do_not(self) -> None:
        result = scan_v2_setups(**long_fixture(inst_category="1"))

        entries = list(result.index[result["enter_long"] == 1])
        self.assertEqual(entries, [1])
        self.assertLess(result.loc[1, "initial_stop_price"], 100.0)

    def test_short_entry_is_symmetric(self) -> None:
        result = scan_v2_setups(**short_fixture())

        self.assertEqual(list(result.index[result["enter_short"] == 1]), [1])
        self.assertGreater(result.loc[1, "initial_stop_price"], 100.0)

    def test_pullback_after_wait_limit_is_ignored(self) -> None:
        fixture = long_fixture(inst_category="1")
        fixture["params"] = V2Parameters(pullback_wait_15m=2)
        fixture["candles15m"].loc[1:2, "low"] = 100.6
        fixture["candles15m"].loc[3, ["low", "close"]] = [99.99, 100.2]

        result = scan_v2_setups(**fixture)

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_pair_outside_hourly_universe_cannot_enter(self) -> None:
        fixture = long_fixture(inst_category="1")
        fixture["universe_mask"] = fixture["universe_mask"].iloc[0:0]

        result = scan_v2_setups(**fixture)

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_missing_informative_data_is_not_treated_as_true(self) -> None:
        fixture = long_fixture(inst_category="1")
        fixture["candles4h"] = fixture["candles4h"].iloc[1:]

        result = scan_v2_setups(**fixture)

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_mixed_datetime_resolutions_merge_without_error(self) -> None:
        fixture = long_fixture(inst_category="1")
        fixture["candles15m"]["date"] = fixture["candles15m"]["date"].dt.as_unit("ms")
        fixture["candles1h"]["date"] = fixture["candles1h"]["date"].dt.as_unit("us")

        result = scan_v2_setups(**fixture)

        self.assertEqual(result["enter_long"].sum(), 1)

    def test_appending_future_rows_does_not_change_historical_signals(self) -> None:
        fixture = long_fixture(inst_category="1")
        old = scan_v2_setups(**fixture)
        extended_fixture = {**fixture, "candles15m": append_future(fixture["candles15m"])}
        extended = scan_v2_setups(**extended_fixture)

        pd.testing.assert_frame_equal(
            old[["enter_long", "enter_short", "initial_stop_price"]],
            extended.loc[old.index, ["enter_long", "enter_short", "initial_stop_price"]],
        )


def informative_frame(direction: str, timeframe: str) -> pd.DataFrame:
    hours = 4 if timeframe == "4h" else 1
    start = "2024-12-31 20:00" if timeframe == "4h" else "2025-01-01"
    dates = pd.date_range(start, periods=2, freq=f"{hours}h", tz="UTC")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [100.0, 100.0],
            "high": [100.1, 101.1],
            "low": [99.9, 99.9],
            "close": [100.0, 101.0],
            "volume": 1.0,
            "quote_volume": 1_000_000.0,
            "atr14": [1.0, 1.0],
            "trend_long": direction == "long",
            "trend_short": direction == "short",
        }
    )
    for column in SIX:
        frame[column] = [100.0, 100.0]
    return frame


def setup_1h(side: str) -> pd.DataFrame:
    frame = informative_frame("long" if side == "long" else "short", "1h")
    frame.loc[1, "close"] = 101.0 if side == "long" else 99.0
    frame.loc[1, "high"] = 101.1
    frame.loc[1, "low"] = 98.9 if side == "short" else 99.9
    return frame


def reference_4h(direction: str) -> pd.DataFrame:
    return informative_frame(direction, "4h")


def base_15m(side: str) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01 02:00", periods=6, freq="15min", tz="UTC")
    if side == "long":
        lows = [100.6, 99.99, 99.98, 100.6, 100.6, 100.6]
        closes = [100.8, 100.2, 100.3, 100.8, 100.9, 101.0]
    else:
        lows = [99.2, 99.6, 99.7, 99.1, 99.0, 98.9]
        closes = [99.2, 99.8, 99.7, 99.2, 99.1, 99.0]
    highs = [close + 0.2 for close in closes]
    if side == "short":
        highs[1] = 100.01
        highs[2] = 100.02
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
            "quote_volume": 1_000_000.0,
            "atr14": 1.0,
            "ema20": closes,
        }
    )


def universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01 02:00", periods=2, freq="1h", tz="UTC"),
            "pair": PAIR,
            "rank": 1,
            "score": 1.0,
            "eligible": True,
        }
    )


def long_fixture(
    inst_category: str, btc_regime: str = "long", eth_regime: str = "neutral"
) -> dict:
    return {
        "pair": PAIR,
        "inst_category": inst_category,
        "candles15m": base_15m("long"),
        "candles1h": setup_1h("long"),
        "candles4h": informative_frame("long", "4h"),
        "btc4h": reference_4h(btc_regime),
        "eth4h": reference_4h(eth_regime),
        "universe_mask": universe(),
        "params": V2Parameters(),
    }


def short_fixture() -> dict:
    return {
        "pair": PAIR,
        "inst_category": "1",
        "candles15m": base_15m("short"),
        "candles1h": setup_1h("short"),
        "candles4h": informative_frame("short", "4h"),
        "btc4h": reference_4h("short"),
        "eth4h": reference_4h("neutral"),
        "universe_mask": universe(),
        "params": V2Parameters(),
    }


def append_future(frame: pd.DataFrame) -> pd.DataFrame:
    future = frame.iloc[-1:].copy()
    future["date"] = future["date"] + pd.Timedelta(minutes=15)
    future[["open", "high", "low", "close"]] = [101.0, 101.2, 100.8, 101.1]
    return pd.concat([frame, future], ignore_index=True)


if __name__ == "__main__":
    unittest.main()
