from __future__ import annotations

import unittest

import pandas as pd

from scripts.export_okx_v2_freqtrade_data import freqtrade_stem, funding_ohlcv


class ExportOkxV2FreqtradeDataTests(unittest.TestCase):
    def test_symbol_and_funding_layout_match_freqtrade_convention(self) -> None:
        candles = pd.DataFrame(
            {"date": pd.date_range("2025-01-01", periods=2, freq="15min", tz="UTC")}
        )
        funding = pd.DataFrame({"date": [candles.loc[1, "date"]], "rate": [0.001]})

        result = funding_ohlcv(candles, funding)

        self.assertEqual(freqtrade_stem("XRP/USDT:USDT"), "XRP_USDT_USDT")
        self.assertEqual(result["close"].tolist(), [0.0, 0.001])
        self.assertEqual(
            result.columns.tolist(), ["date", "open", "high", "low", "close", "volume"]
        )


if __name__ == "__main__":
    unittest.main()
