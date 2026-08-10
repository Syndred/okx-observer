from __future__ import annotations

from io import BytesIO
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from scripts.download_binance_archive import _overlay_funding, parse_funding_zip, parse_kline_zip


def zipped_csv(name: str, contents: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(name, contents)
    return buffer.getvalue()


class BinanceArchiveTests(unittest.TestCase):
    def test_parses_headered_kline_archive_to_freqtrade_columns(self) -> None:
        payload = zipped_csv(
            "BTCUSDT-1h-2025-01.csv",
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore\n"
            "1735689600000,100,102,99,101,12,1735693199999,0,1,0,0,0\n",
        )

        result = parse_kline_zip(payload)

        self.assertEqual(list(result.columns), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(result.iloc[0]["close"], 101.0)
        self.assertEqual(result.iloc[0]["date"].isoformat(), "2025-01-01T00:00:00+00:00")

    def test_parses_funding_archive_and_aligns_rate_to_hour(self) -> None:
        payload = zipped_csv(
            "BTCUSDT-fundingRate-2025-01.csv",
            "calc_time,funding_interval_hours,last_funding_rate\n"
            "1735689600015,8,0.00010000\n",
        )

        result = parse_funding_zip(payload)

        self.assertEqual(result.iloc[0]["date"].isoformat(), "2025-01-01T00:00:00+00:00")
        self.assertEqual(result.iloc[0]["open"], 0.0001)
        self.assertEqual(result.iloc[0]["volume"], 0.0)

    def test_funding_overlay_preserves_non_eight_hour_intervals(self) -> None:
        dates = pd.date_range("2025-01-01", periods=9, freq="1h", tz="UTC")
        contract = pd.DataFrame(
            {
                "date": dates,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1.0,
            }
        )
        actual = pd.DataFrame(
            {
                "date": dates[[0, 4, 8]],
                "open": [0.0001, 0.0002, 0.0003],
                "high": [0.0001, 0.0002, 0.0003],
                "low": [0.0001, 0.0002, 0.0003],
                "close": [0.0001, 0.0002, 0.0003],
                "volume": 0.0,
            }
        )

        result, fallback_rows = _overlay_funding(contract, actual)

        self.assertEqual(len(result), 9)
        self.assertEqual(result.loc[4, "open"], 0.0002)
        self.assertEqual(fallback_rows, 6)


if __name__ == "__main__":
    unittest.main()
