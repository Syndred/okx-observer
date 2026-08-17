from __future__ import annotations

import unittest

from scripts.scan_okx_three_stage import build_scanner_universe
from scripts.snapshot_okx_instruments import DAY_MS, normalize_instruments


class OkxInstrumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 50 * DAY_MS
        self.payload = {
            "code": "0",
            "data": [
                {
                    "instId": "OLD-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": str(10 * DAY_MS),
                    "instCategory": "1",
                    "ctVal": "10",
                    "ctValCcy": "OLD",
                    "tickSz": "0.001",
                    "lotSz": "1",
                    "minSz": "1",
                    "lever": "20",
                },
                {
                    "instId": "NEW-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": str(40 * DAY_MS),
                    "instCategory": "1",
                },
                {
                    "instId": "OLD-USD-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USD",
                    "state": "live",
                    "listTime": "0",
                    "instCategory": "1",
                },
                {
                    "instId": "DELIST-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "suspend",
                    "listTime": "0",
                    "instCategory": "1",
                },
            ],
        }

    def test_only_live_usdt_swaps_older_than_30_days_are_eligible(self) -> None:
        rows = normalize_instruments(
            self.payload, now_ms=self.now_ms, min_age_days=30
        )

        eligible = [row["instId"] for row in rows if row["eligible"]]
        self.assertEqual(eligible, ["OLD-USDT-SWAP"])
        reasons = {row["instId"]: row["eligibility_reason"] for row in rows}
        self.assertEqual(reasons["NEW-USDT-SWAP"], "listed_less_than_30_days")
        self.assertEqual(reasons["DELIST-USDT-SWAP"], "not_live")

    def test_recent_live_crypto_contract_is_eligible_by_default_and_age_is_diagnostic(
        self,
    ) -> None:
        payload = {
            "code": "0",
            "data": [
                {
                    "instId": "RECENT-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": str(49 * DAY_MS),
                    "instCategory": "1",
                }
            ],
        }

        rows = normalize_instruments(payload, now_ms=self.now_ms)

        self.assertEqual(rows[0]["ageDays"], 1)
        self.assertTrue(rows[0]["eligible"])
        self.assertEqual(rows[0]["eligibility_reason"], "eligible")

    def test_scanner_marks_btc_eth_as_trade_with_market_reference(self) -> None:
        payload = {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": "0",
                    "instCategory": "1",
                },
                {
                    "instId": "ETH-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": "0",
                    "instCategory": "1",
                },
                {
                    "instId": "KO-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": "0",
                    "instCategory": "3",
                },
            ],
        }

        rows = normalize_instruments(payload, now_ms=self.now_ms)
        rows, _ = build_scanner_universe(
            rows,
            {"BTCUSDT", "ETHUSDT", "KOUSDT"},
        )
        by_id = {row["instId"]: row for row in rows}

        self.assertEqual(by_id["BTC-USDT-SWAP"]["role"], "trade")
        self.assertTrue(by_id["BTC-USDT-SWAP"]["market_reference"])
        self.assertEqual(by_id["ETH-USDT-SWAP"]["role"], "trade")
        self.assertTrue(by_id["ETH-USDT-SWAP"]["market_reference"])
        self.assertTrue(by_id["BTC-USDT-SWAP"]["scanner_eligible"])
        self.assertTrue(by_id["ETH-USDT-SWAP"]["scanner_eligible"])
        self.assertEqual(by_id["KO-USDT-SWAP"]["role"], "trade")
        self.assertEqual(by_id["KO-USDT-SWAP"]["instCategory"], "3")
        self.assertFalse(by_id["KO-USDT-SWAP"]["scanner_eligible"])

    def test_unknown_category_is_rejected_without_guessing(self) -> None:
        payload = {
            "code": "0",
            "data": [
                {
                    "instId": "MYSTERY-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "state": "live",
                    "listTime": "0",
                    "instCategory": "",
                }
            ],
        }

        result = normalize_instruments(payload, now_ms=self.now_ms)

        self.assertFalse(result[0]["eligible"])
        self.assertEqual(result[0]["eligibility_reason"], "unknown_category")


if __name__ == "__main__":
    unittest.main()
