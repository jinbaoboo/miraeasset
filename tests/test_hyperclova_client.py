import json
import urllib.error
import unittest
from unittest.mock import patch

from src.agent.hyperclova_client import HyperClovaClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class HyperClovaClientTests(unittest.TestCase):
    def _client(self, **overrides):
        env = {
            "HCX_ENDPOINT": "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005",
            "HCX_API_KEY": "test-secret-key",
            "HCX_APIGW_KEY": "",
            "HCX_MAX_RETRIES": "0",
            "HCX_RETRY_DELAY_SECONDS": "0",
        }
        env.update(overrides)
        return patch.dict("os.environ", env, clear=True)

    def test_v3_request_uses_bearer_and_repetition_penalty(self):
        with self._client():
            client = HyperClovaClient()
        response = {"status": {"code": "20000"},
                    "result": {"message": {"role": "assistant", "content": "답변 [근거 1]"}}}
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)) as urlopen:
            output = client.generate("질문", [{"content": "근거", "citation": {"rcept_no": "1"}}])
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(output, "답변 [근거 1]")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret-key")
        self.assertIsNone(request.get_header("X-ncp-clovastudio-api-key"))
        self.assertEqual(payload["repetitionPenalty"], 1.1)
        self.assertNotIn("repeatPenalty", payload)

    def test_retryable_http_error_is_retried(self):
        with self._client(HCX_MAX_RETRIES="1"):
            client = HyperClovaClient()
        rate_limit = urllib.error.HTTPError(client.endpoint, 429, "rate limited", {}, None)
        success = FakeResponse({"result": {"message": {"content": "복구됨 [근거 1]"}}})
        with patch("urllib.request.urlopen", side_effect=[rate_limit, success]) as urlopen, patch("time.sleep") as sleep:
            output = client.generate("질문", [{"content": "근거", "citation": {}}])
        self.assertEqual(output, "복구됨 [근거 1]")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_api_error_does_not_expose_api_key(self):
        with self._client():
            client = HyperClovaClient()
        response = {"status": {"code": "40003", "message": "Unauthorized"}, "result": {}}
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)):
            with self.assertRaises(RuntimeError) as raised:
                client.generate("질문", [{"content": "근거", "citation": {}}])
        self.assertIn("API status 40003", str(raised.exception))
        self.assertNotIn("test-secret-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
