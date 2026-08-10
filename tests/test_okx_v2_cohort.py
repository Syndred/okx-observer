from __future__ import annotations

import unittest

import pandas as pd

from scripts.select_okx_v2_cohort import selected_instruments


class OkxV2CohortTests(unittest.TestCase):
    def test_requires_twenty_four_selected_hours_and_excludes_reference(self) -> None:
        mask = pd.DataFrame(
            {
                "pair": ["ALT/USDT:USDT"] * 24 + ["TINY/USDT:USDT"] * 23 + ["BTC/USDT:USDT"] * 30
            }
        )
        snapshot = {
            "instruments": [
                {"symbol": "ALT/USDT:USDT", "instId": "ALT-USDT-SWAP", "eligible": True, "role": "trade"},
                {"symbol": "TINY/USDT:USDT", "instId": "TINY-USDT-SWAP", "eligible": True, "role": "trade"},
                {"symbol": "BTC/USDT:USDT", "instId": "BTC-USDT-SWAP", "eligible": True, "role": "reference"},
            ]
        }

        self.assertEqual(selected_instruments(mask, snapshot, 24), ["ALT-USDT-SWAP"])


if __name__ == "__main__":
    unittest.main()
