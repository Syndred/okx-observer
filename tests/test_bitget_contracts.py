from __future__ import annotations

import unittest

from scripts.bitget_contracts import normalize_bitget_contracts


class BitgetContractTests(unittest.TestCase):
    def test_only_normal_usdt_perpetual_contracts_are_available(self) -> None:
        payload = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                },
                {
                    "symbol": "ETHUSDT",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                },
                {
                    "symbol": "OFCUSDT",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                },
                {
                    "symbol": "BTCUSD",
                    "quoteCoin": "USD",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                },
                {
                    "symbol": "BTCUSDT_DELIVERY",
                    "quoteCoin": "USDT",
                    "symbolType": "delivery",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                },
                {
                    "symbol": "ETHUSDT_OFF",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "123",
                },
                {
                    "symbol": "SOLUSDT_SUSPENDED",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "offline",
                    "offTime": "-1",
                },
            ],
        }

        contracts = normalize_bitget_contracts(payload)

        self.assertEqual(set(contracts), {"BTCUSDT", "ETHUSDT", "OFCUSDT"})

    def test_empty_success_response_is_fatal(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "BTCUSDT.*ETHUSDT"):
            normalize_bitget_contracts({"code": "00000", "data": []})

    def test_success_response_missing_market_references_is_fatal(self) -> None:
        payload = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "quoteCoin": "USDT",
                    "symbolType": "perpetual",
                    "symbolStatus": "normal",
                    "offTime": "-1",
                }
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "ETHUSDT"):
            normalize_bitget_contracts(payload)

    def test_non_success_response_is_a_scan_fatal_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Bitget"):
            normalize_bitget_contracts({"code": "400", "msg": "rate limited"})


if __name__ == "__main__":
    unittest.main()
