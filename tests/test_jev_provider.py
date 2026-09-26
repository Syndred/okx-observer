from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import Mock, patch

import requests

from user_data.strategy_lib.jev_provider import JEV_ENDPOINT, JevClient, JevProviderError


STATE = {"execution_rules": {"take_profit_r": 3, "max_holding_hours": 24}, "as_of": "2026-01-01"}
BODY = {"model": "jev-1.13.0", "answers": {"trade_outcome": {
    "type": "choice", "choice": "win", "probabilities": {"win": 0.7, "loss": 0.3}, "confidence": 0.8,
}}}


class JevProviderTests(unittest.TestCase):
    def response(self, body=None, status=200):
        return Mock(status_code=status, json=Mock(return_value=BODY if body is None else body))

    def test_request_and_probability_contract(self):
        response = self.response()
        with patch("user_data.strategy_lib.jev_provider.requests.post", return_value=response) as post:
            result = JevClient("secret", timeout=7).evaluate(STATE)
        self.assertEqual(result, {"model": "jev-1.13.0", "probability": 0.7, "confidence": 0.8})
        args, kwargs = post.call_args
        self.assertEqual(args, (JEV_ENDPOINT,))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(kwargs["json"]["state"], STATE)
        self.assertEqual(set(kwargs["json"]["questions"]["trade_outcome"]["criteria"]), {"win", "loss"})
        self.assertIn("state.execution_rules", kwargs["json"]["questions"]["trade_outcome"]["instructions"])
        self.assertEqual(kwargs["timeout"], 7)
        self.assertFalse(kwargs["allow_redirects"])
        response.close.assert_called_once()

    def test_confidence_not_invented(self):
        body = copy.deepcopy(BODY)
        del body["answers"]["trade_outcome"]["confidence"]
        with patch("user_data.strategy_lib.jev_provider.requests.post", return_value=self.response(body)):
            self.assertIsNone(JevClient("secret").evaluate(STATE)["confidence"])

    def test_environment_priority_and_blank_fallback(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "first", "JEV_API_KEY": "second"}, clear=True):
            self.assertEqual(JevClient()._api_key, "first")
            self.assertEqual(JevClient("explicit")._api_key, "explicit")
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": " ", "JEV_API_KEY": "second"}, clear=True):
            self.assertEqual(JevClient()._api_key, "second")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(JevProviderError, "missing_key"):
                JevClient()

    def test_invalid_probabilities_and_confidence(self):
        for field in ("win", "loss", "confidence"):
            for value in (float("nan"), float("inf"), -0.1, 1.1, True, "0.7"):
                with self.subTest(field=field, value=value):
                    body = copy.deepcopy(BODY)
                    answer = body["answers"]["trade_outcome"]
                    (answer if field == "confidence" else answer["probabilities"])[field] = value
                    with patch("user_data.strategy_lib.jev_provider.requests.post", return_value=self.response(body)):
                        with self.assertRaisesRegex(JevProviderError, "invalid_response"):
                            JevClient("secret").evaluate(STATE)

    def test_bad_response_shapes(self):
        bodies = [[], {}, {"model": "x", "answers": {}}, copy.deepcopy(BODY), copy.deepcopy(BODY)]
        bodies[-2]["answers"]["trade_outcome"]["probabilities"] = {"win": 0.7, "loss": 0.9}
        bodies[-1]["answers"]["trade_outcome"]["choice"] = "other"
        for body in bodies:
            with self.subTest(body=body), patch("user_data.strategy_lib.jev_provider.requests.post", return_value=self.response(body)):
                with self.assertRaisesRegex(JevProviderError, "invalid_response"):
                    JevClient("secret").evaluate(STATE)

    def test_network_and_http_errors_are_sanitized(self):
        for exception, reason in [(requests.Timeout("secret body"), "timeout"), (requests.ConnectionError("secret body"), "network_error")]:
            with patch("user_data.strategy_lib.jev_provider.requests.post", side_effect=exception):
                with self.assertRaises(JevProviderError) as caught:
                    JevClient("secret").evaluate(STATE)
                self.assertEqual(caught.exception.reason, reason)
                self.assertNotIn("secret", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
        for status, reason in [(429, "rate_limited"), (401, "http_error"), (500, "http_error"), (302, "http_error")]:
            response = self.response(status=status)
            with patch("user_data.strategy_lib.jev_provider.requests.post", return_value=response):
                with self.assertRaisesRegex(JevProviderError, reason):
                    JevClient("secret").evaluate(STATE)
            response.json.assert_not_called()
            response.close.assert_called_once()

    def test_invalid_json(self):
        response = self.response()
        response.json.side_effect = ValueError("private response body")
        with patch("user_data.strategy_lib.jev_provider.requests.post", return_value=response):
            with self.assertRaisesRegex(JevProviderError, "invalid_response"):
                JevClient("secret").evaluate(STATE)

    def test_reject_invalid_state_before_network(self):
        with patch("user_data.strategy_lib.jev_provider.requests.post") as post:
            for state in ({}, [], {"execution_rules": {}}, dict(STATE, feature=float("nan"))):
                with self.assertRaisesRegex(JevProviderError, "invalid_state"):
                    JevClient("secret").evaluate(state)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
