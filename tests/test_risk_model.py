from __future__ import annotations

import unittest

from user_data.strategies.risk_model import collateral_for_risk


class RiskModelTests(unittest.TestCase):
    def test_sizes_collateral_from_equity_risk_stop_and_leverage(self) -> None:
        collateral = collateral_for_risk(
            equity=100.0,
            risk_pct=0.01,
            stop_distance_ratio=0.01,
            leverage=5.0,
            max_collateral=100.0,
        )

        self.assertAlmostEqual(collateral, 20.0)

    def test_caps_collateral_at_available_balance(self) -> None:
        collateral = collateral_for_risk(
            equity=100.0,
            risk_pct=0.05,
            stop_distance_ratio=0.005,
            leverage=3.0,
            max_collateral=33.0,
        )

        self.assertAlmostEqual(collateral, 33.0)

    def test_rejects_non_positive_stop_distance(self) -> None:
        for stop_distance in (0.0, -0.01):
            with self.subTest(stop_distance=stop_distance):
                with self.assertRaises(ValueError):
                    collateral_for_risk(
                        equity=100.0,
                        risk_pct=0.01,
                        stop_distance_ratio=stop_distance,
                        leverage=5.0,
                        max_collateral=100.0,
                    )


if __name__ == "__main__":
    unittest.main()
