import json
import time
import unittest

from local_coding_agent.ollama_adapter import (
    ModelProfile,
    OllamaClient,
    OllamaError,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, path, body, headers, timeout):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StreamingFakeTransport:
    """Transport with both request() and stream(); stream yields chunks."""

    def __init__(self, chunk_bytes):
        self.chunk_bytes = list(chunk_bytes)
        self.requests = []

    def request(self, method, path, body, headers, timeout):
        self.requests.append((method, path, body, headers, timeout))
        raise AssertionError("buffered request should not be called for streaming path")

    def stream(self, method, path, body, headers, timeout):
        self.requests.append((method, path, body, headers, timeout))
        for chunk in self.chunk_bytes:
            yield chunk


class OllamaClientTests(unittest.TestCase):
    def setUp(self):
        self.profile = ModelProfile(
            name="small-coder",
            model="qwen2.5:1.5b",
            endpoint="http://127.0.0.1:11434",
            think=False,
            temperature=0,
            num_ctx=4096,
            num_predict=256,
            keep_alive="10m",
            timeout_seconds=7,
        )

    def test_chat_sends_utf8_request_with_profile_limits(self):
        transport = FakeTransport(
            [
                (
                    200,
                    json.dumps(
                        {"message": {"role": "assistant", "content": "готово"}},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
            ]
        )
        client = OllamaClient(self.profile, transport=transport)

        result = client.chat(
            [{"role": "user", "content": "Исправь русский текст"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )

        self.assertEqual(result["message"]["content"], "готово")
        request = transport.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/chat")
        self.assertEqual(request["headers"]["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(request["timeout"], 7)
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(payload["model"], "qwen2.5:1.5b")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], "10m")
        self.assertEqual(payload["options"], {"temperature": 0, "num_ctx": 4096, "num_predict": 256})
        self.assertEqual(payload["messages"][0]["content"], "Исправь русский текст")
        self.assertEqual(payload["tools"][0]["function"]["name"], "read_file")

    def test_loaded_models_uses_get_api_ps_and_returns_json(self):
        transport = FakeTransport([(200, b'{"models":[{"name":"qwen2.5:1.5b"}]}')])
        client = OllamaClient(self.profile, transport=transport)

        result = client.loaded_models()

        self.assertEqual(result["models"][0]["name"], "qwen2.5:1.5b")
        self.assertEqual(transport.requests[0]["method"], "GET")
        self.assertEqual(transport.requests[0]["path"], "/api/ps")
        self.assertIsNone(transport.requests[0]["body"])

    def test_available_models_uses_get_api_tags_and_returns_json(self):
        transport = FakeTransport([(200, b'{"models":[{"name":"qwen2.5:1.5b"}]}')])
        client = OllamaClient(self.profile, transport=transport)

        result = client.available_models()

        self.assertEqual(result["models"][0]["name"], "qwen2.5:1.5b")
        self.assertEqual(transport.requests[0]["method"], "GET")
        self.assertEqual(transport.requests[0]["path"], "/api/tags")
        self.assertIsNone(transport.requests[0]["body"])

    def test_unload_model_requests_zero_keep_alive(self):
        transport = FakeTransport([(200, b'{"done":true}')])
        client = OllamaClient(self.profile, transport=transport)

        result = client.unload_model()

        self.assertTrue(result["done"])
        request = transport.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/generate")
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(payload, {"model": "qwen2.5:1.5b", "stream": False, "keep_alive": 0})

    def test_http_and_invalid_json_fail_as_normalized_ollama_errors(self):
        http_transport = FakeTransport([(503, b'{"error":"model unavailable"}')])
        client = OllamaClient(self.profile, transport=http_transport)

        with self.assertRaisesRegex(OllamaError, "HTTP 503") as http_error:
            client.chat([])
        self.assertEqual(http_error.exception.kind, "http")

        invalid_transport = FakeTransport([(200, b"not-json")])
        client = OllamaClient(self.profile, transport=invalid_transport)

        with self.assertRaisesRegex(OllamaError, "invalid JSON") as json_error:
            client.loaded_models()
        self.assertEqual(json_error.exception.kind, "invalid_json")


    def test_chat_sends_sampling_options_when_configured(self):
        profile = ModelProfile(
            name="sampling-coder",
            model="local-qwen3-8b-q6k:latest",
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            min_p=0.05,
            presence_penalty=1.5,
            frequency_penalty=0.5,
            repeat_penalty=1.1,
            seed=1234,
            stop=("</s>", "###"),
        )
        transport = FakeTransport(
            [
                (
                    200,
                    json.dumps(
                        {"message": {"role": "assistant", "content": "done"}},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
            ]
        )
        client = OllamaClient(profile, transport=transport)
        client.chat([{"role": "user", "content": "hi"}])

        payload = json.loads(transport.requests[0]["body"].decode("utf-8"))
        options = payload["options"]
        self.assertEqual(options["temperature"], 0.7)
        self.assertEqual(options["top_p"], 0.8)
        self.assertEqual(options["top_k"], 40)
        self.assertEqual(options["min_p"], 0.05)
        self.assertEqual(options["presence_penalty"], 1.5)
        self.assertEqual(options["frequency_penalty"], 0.5)
        self.assertEqual(options["repeat_penalty"], 1.1)
        self.assertEqual(options["seed"], 1234)
        self.assertEqual(options["stop"], ["</s>", "###"])

    def test_streaming_accumulates_content_and_tool_calls(self):
        profile = ModelProfile(
            name="small-coder",
            model="qwen2.5:1.5b",
            stream_idle_timeout_seconds=5.0,
        )
        chunks = [
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hel",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "read_file", "arguments": '{"pa'},
                            }
                        ],
                    }
                }
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": "lo",
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "", "arguments": 'th": "a.py"}'},
                            }
                        ],
                    }
                }
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {
                    "message": {"role": "assistant", "content": ""},
                    "prompt_eval_count": 10,
                    "eval_count": 20,
                    "prompt_eval_duration": 1000000,
                    "eval_duration": 2000000,
                    "total_duration": 3000000,
                    "load_duration": 500000,
                    "done": True,
                }
            ).encode("utf-8")
            + b"\n",
        ]
        transport = StreamingFakeTransport(chunks)
        client = OllamaClient(profile, transport=transport)

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(result["message"]["content"], "Hello")
        self.assertEqual(len(result["message"]["tool_calls"]), 1)
        call = result["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "read_file")
        self.assertEqual(call["function"]["arguments"], '{"path": "a.py"}')
        self.assertEqual(result["prompt_eval_count"], 10)
        self.assertEqual(result["eval_count"], 20)
        self.assertEqual(result["prompt_eval_duration"], 1000000)
        self.assertEqual(result["eval_duration"], 2000000)
        self.assertEqual(result["total_duration"], 3000000)
        self.assertEqual(result["load_duration"], 500000)

        payload = json.loads(transport.requests[0][2].decode("utf-8"))
        self.assertTrue(payload["stream"])

    def test_streaming_without_done_raises_stream_closed(self):
        profile = ModelProfile(name="small-coder", model="qwen2.5:1.5b")
        chunks = [
            json.dumps({"message": {"role": "assistant", "content": "hi"}}).encode("utf-8")
            + b"\n"
        ]
        client = OllamaClient(profile, transport=StreamingFakeTransport(chunks))

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.kind, "stream_closed")

    def test_streaming_idle_timeout_raises_timeout(self):
        profile = ModelProfile(
            name="small-coder",
            model="qwen2.5:1.5b",
            stream_idle_timeout_seconds=0.05,
        )

        class SlowTransport:
            def __init__(self):
                self.requests = []

            def request(self, method, path, body, headers, timeout):
                self.requests.append((method, path))
                raise AssertionError("buffered path should not be used")

            def stream(self, method, path, body, headers, timeout):
                self.requests.append((method, path))
                yield json.dumps({"message": {"role": "assistant", "content": "a"}}).encode("utf-8") + b"\n"
                time.sleep(0.2)
                yield json.dumps({"message": {"role": "assistant", "content": "b"}, "done": True}).encode("utf-8") + b"\n"

        client = OllamaClient(profile, transport=SlowTransport())

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_streaming_parses_multiple_ndjson_lines_in_one_chunk(self):
        profile = ModelProfile(name="small-coder", model="qwen2.5:1.5b")
        chunk = (
            json.dumps({"message": {"role": "assistant", "content": "Hel"}}).encode("utf-8")
            + b"\n"
            + json.dumps({"message": {"role": "assistant", "content": "lo"}}).encode("utf-8")
            + b"\n"
            + json.dumps(
                {"message": {"role": "assistant", "content": ""}, "done": True}
            ).encode("utf-8")
            + b"\n"
        )
        client = OllamaClient(profile, transport=StreamingFakeTransport([chunk]))

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(result["message"]["content"], "Hello")

    def test_streaming_uses_idle_timeout_as_socket_timeout(self):
        profile = ModelProfile(
            name="small-coder",
            model="qwen2.5:1.5b",
            timeout_seconds=7,
            stream_idle_timeout_seconds=5.0,
        )
        chunks = [
            json.dumps({"message": {"role": "assistant", "content": "ok"}, "done": True}).encode("utf-8")
            + b"\n"
        ]
        transport = StreamingFakeTransport(chunks)
        client = OllamaClient(profile, transport=transport)

        client.chat([{"role": "user", "content": "hi"}])

        method, path, body, headers, timeout = transport.requests[0]
        self.assertEqual(timeout, 5.0)
        self.assertNotEqual(timeout, 7)


if __name__ == "__main__":
    unittest.main()
