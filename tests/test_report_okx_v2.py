from __future__ import annotations

import unittest

import pandas as pd

from scripts.report_okx_v2 import threshold_crossings


class ReportOkxV2Tests(unittest.TestCase):
    def test_threshold_crossings_return_first_date_or_never(self) -> None:
        curve = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=3, freq="1h", tz="UTC"),
                "equity": [100.0, 1001.0, 900.0],
            }
        )

        result = threshold_crossings(curve, (1_000, 10_000))

        self.assertEqual(result[1_000], "2025-01-01T01:00:00+00:00")
        self.assertEqual(result[10_000], "never")


if __name__ == "__main__":
    unittest.main()
