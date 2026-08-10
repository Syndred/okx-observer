from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.okx_candles import (
    confirmed_candles,
    funding_events,
    mark_candles,
    resample_confirmed,
)


def row(timestamp: int, price: float, confirm: str = "1") -> list[str]:
    return [
        str(timestamp),
        str(price),
        str(price + 1),
        str(price - 1),
        str(price + 0.5),
        "10",
        "5",
        "500",
        confirm,
    ]


class OkxCandleTests(unittest.TestCase):
    def test_unconfirmed_last_candle_is_removed_and_rows_are_sorted(self) -> None:
        payload = [row(900_000, 101, "0"), row(0, 100), row(900_000, 101)]

        result = confirmed_candles(payload)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["date"].isoformat(), "1970-01-01T00:00:00+00:00")
        self.assertEqual(result.iloc[1]["quote_volume"], 500.0)

    def test_resampling_requires_every_confirmed_15m_child(self) -> None:
        incomplete = confirmed_candles([row(0, 100), row(900_000, 101), row(1_800_000, 102)])

        result = resample_confirmed(incomplete, "1h")

        self.assertTrue(result.empty)

    def test_resampling_builds_utc_aligned_ohlcv(self) -> None:
        complete = confirmed_candles(
            [row(0, 100), row(900_000, 101), row(1_800_000, 102), row(2_700_000, 103)]
        )

        result = resample_confirmed(complete, "1h")

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["open"], 100.0)
        self.assertEqual(result.iloc[0]["high"], 104.0)
        self.assertEqual(result.iloc[0]["low"], 99.0)
        self.assertEqual(result.iloc[0]["close"], 103.5)
        self.assertEqual(result.iloc[0]["volume"], 20.0)
        self.assertEqual(result.iloc[0]["quote_volume"], 2_000.0)
        self.assertEqual(result.iloc[0]["date"], pd.Timestamp("1970-01-01", tz="UTC"))

    def test_duplicate_timestamp_does_not_fake_complete_hour(self) -> None:
        duplicate = confirmed_candles(
            [row(0, 100), row(900_000, 101), row(1_800_000, 102), row(1_800_000, 999)]
        )

        result = resample_confirmed(duplicate, "1h")

        self.assertTrue(result.empty)

    def test_mark_parser_removes_unconfirmed_candle(self) -> None:
        result = mark_candles(
            [
                ["900000", "101", "102", "100", "101.5", "0"],
                ["0", "100", "101", "99", "100.5", "1"],
            ]
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["close"], 100.5)
        self.assertEqual(result.iloc[0]["volume"], 0.0)

    def test_funding_parser_uses_realized_rate_and_sorts(self) -> None:
        result = funding_events(
            [
                {"fundingTime": "28800000", "fundingRate": "0.1", "realizedRate": "0.002"},
                {"fundingTime": "0", "fundingRate": "0.001", "realizedRate": ""},
            ]
        )

        self.assertEqual(list(result["rate"]), [0.001, 0.002])


if __name__ == "__main__":
    unittest.main()
