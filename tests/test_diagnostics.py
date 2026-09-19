import json
import io
import unittest
import urllib.error
from unittest.mock import patch
from app.diagnostics import check_public_source


class DiagnosticsTests(unittest.TestCase):
    def test_http_rejection_without_response_disclosure(self):
        failure = urllib.error.HTTPError("https://example.invalid/private", 403,
                                         "private response detail", {}, io.BytesIO(
                                             b'{"code":"invalid_request","parameter":"user","error":"private response detail"}'))
        with patch("app.providers.urllib.request.urlopen", side_effect=failure) as request:
            result = check_public_source()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "http_error")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["upstream_code"], "invalid_request")
        self.assertEqual(result["upstream_parameter"], "user")
        self.assertTrue(result["tls_verification"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(request.call_count, 1)
