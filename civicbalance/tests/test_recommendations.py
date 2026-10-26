import io
import json
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recommendations

TEST_KEY = "sk-test-key-for-automated-checks-only"
RESULT = {
    "user_reflection": "You described fatigue. You find gardening restorative. A short news window could leave more time for it.",
    "primary_stressor_category": "News Fatigue",
    "strategies": [{"title": f"Idea {i}", "description": "Make room for the activity you described."} for i in range(3)],
}


def envelope(result=RESULT):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(result)},
    ]}]}


class RecommendationTests(unittest.TestCase):
    def test_request_uses_key_only_as_authorization_with_structured_nonstored_output(self):
        context = {"synthetic": "gardening"}
        with patch.object(recommendations, "urlopen", return_value=io.BytesIO(json.dumps(envelope()).encode())) as opened:
            self.assertEqual(recommendations.generate_recommendations(TEST_KEY, context), RESULT)
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TEST_KEY}")
        self.assertIs(body["store"], False)
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn(TEST_KEY, request.data.decode())
        self.assertEqual(json.loads(body["input"][0]["content"]), context)
        self.assertEqual(opened.call_args.kwargs["timeout"], 45)
        tls = opened.call_args.kwargs["context"]
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(tls.cert_store_stats()["x509_ca"], 0)

    def test_tls_verification_works_without_system_certificate_paths(self):
        with patch.dict("os.environ", {"SSL_CERT_FILE": "/nonexistent/civicbalance-ca.pem", "SSL_CERT_DIR": "/nonexistent/civicbalance-ca"}):
            tls = recommendations.openai_ssl_context()
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(tls.cert_store_stats()["x509_ca"], 0)

    def test_transport_failures_are_distinguished_without_exposing_details(self):
        for reason, expected_status, expected_message in [
            (ssl.SSLCertVerificationError(1, TEST_KEY), 502, "secure connection"),
            (socket.gaierror(8, TEST_KEY), 502, "DNS"),
            (TimeoutError(TEST_KEY), 504, "too long"),
            (ConnectionRefusedError(TEST_KEY), 502, "proxy, or firewall"),
        ]:
            with self.subTest(reason=type(reason).__name__):
                with patch.object(recommendations, "urlopen", side_effect=URLError(reason)):
                    with self.assertRaises(recommendations.RecommendationError) as caught:
                        recommendations.generate_recommendations(TEST_KEY, {})
                self.assertEqual(caught.exception.status, expected_status)
                self.assertIn(expected_message, str(caught.exception))
                self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_upstream_auth_quota_and_server_errors_are_sanitized(self):
        for upstream_status, expected in ((401, 401), (403, 403), (429, 429), (500, 502)):
            with self.subTest(status=upstream_status):
                error = HTTPError(recommendations.OPENAI_URL, upstream_status, "Unsafe provider message", {}, io.BytesIO(TEST_KEY.encode()))
                with patch.object(recommendations, "urlopen", side_effect=error):
                    with self.assertRaises(recommendations.RecommendationError) as caught:
                        recommendations.generate_recommendations(TEST_KEY, {})
                self.assertEqual(caught.exception.status, expected)
                self.assertNotIn(TEST_KEY, str(caught.exception))
                self.assertNotIn("Unsafe provider message", str(caught.exception))

    def test_incomplete_refused_malformed_or_sensitive_output_is_rejected(self):
        bad = dict(RESULT, user_reflection=TEST_KEY)
        variants = [
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"content": [{"type": "refusal"}]}]},
            envelope({"user_reflection": "Missing required output"}),
            envelope(bad),
            envelope(dict(RESULT, strategies=[])),
        ]
        for value in variants:
            with self.subTest(value=value.get("status")):
                with patch.object(recommendations, "urlopen", return_value=io.BytesIO(json.dumps(value).encode())):
                    with self.assertRaises(recommendations.RecommendationError) as caught:
                        recommendations.generate_recommendations(TEST_KEY, {})
                self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_timeout_is_reported_without_raw_request_details(self):
        with patch.object(recommendations, "urlopen", side_effect=TimeoutError):
            with self.assertRaises(recommendations.RecommendationError) as caught:
                recommendations.generate_recommendations(TEST_KEY, {})
        self.assertEqual(caught.exception.status, 504)


if __name__ == "__main__":
    unittest.main()
