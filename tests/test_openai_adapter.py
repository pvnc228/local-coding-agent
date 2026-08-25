import json
import time
import unittest

from local_coding_agent.ollama_adapter import (
    ModelProfile,
    OpenAICompatibleClient,
    OllamaClient,
    OllamaError,
    build_client,
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


def openai_profile(**overrides):
    kwargs = dict(
        name="ling-tiny",
        model="ling-3.0-tiny-q6k",
        endpoint="http://127.0.0.1:8080",
        provider="openai",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        stop=("<|role_end|>", "<role>"),
    )
    kwargs.update(overrides)
    return ModelProfile(**kwargs)


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_chat_converts_messages_and_normalizes_response(self):
        transport = FakeTransport(
            [
                (
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "id": "call-1",
                                                "function": {
                                                    "name": "read_file",
                                                    "arguments": '{"path": "a.py"}',
                                                },
                                            }
                                        ],
                                    }
                                }
                            ],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                            "timings": {"prompt_ms": 12.5, "predicted_ms": 250.0},
                        }
                    ).encode("utf-8"),
                )
            ]
        )
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        result = client.chat(
            [
                {"role": "system", "content": "contract"},
                {"role": "user", "content": "Исправь"},
                {
                    "role": "tool",
                    "tool_name": "read_file",
                    "tool_call_id": "call-1",
                    "content": "содержимое файла",
                },
            ],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )

        self.assertEqual(result["message"]["role"], "assistant")
        self.assertEqual(result["message"]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(result["prompt_eval_count"], 100)
        self.assertEqual(result["eval_count"], 40)
        self.assertEqual(result["prompt_eval_duration"], 12_500_000)
        self.assertEqual(result["eval_duration"], 250_000_000)

        request = transport.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/chat/completions")
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(payload["model"], "ling-3.0-tiny-q6k")
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["tools"][0]["function"]["name"], "read_file")
        messages = payload["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "contract"})
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool_call_id"], "call-1")

    def test_chat_maps_sampling_options_and_stop(self):
        transport = FakeTransport(
            [
                (
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"role": "assistant", "content": "done"}}
                            ],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    ).encode("utf-8"),
                )
            ]
        )
        client = OpenAICompatibleClient(
            openai_profile(temperature=0.7, top_p=0.8, seed=42), transport=transport
        )
        client.chat([{"role": "user", "content": "hi"}])

        payload = json.loads(transport.requests[0]["body"].decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["top_p"], 0.8)
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(payload["stop"], ["<|role_end|>", "<role>"])
        self.assertEqual(
            payload["chat_template_kwargs"],
            {"thinking_option": "off", "enable_thinking": False},
        )

    def test_available_models_uses_v1_models(self):
        transport = FakeTransport(
            [(200, b'{"data":[{"id":"ling-3.0-tiny-q6k"}]}')]
        )
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        result = client.available_models()

        self.assertEqual(result, {"models": [{"name": "ling-3.0-tiny-q6k"}]})
        self.assertEqual(transport.requests[0]["method"], "GET")
        self.assertEqual(transport.requests[0]["path"], "/v1/models")

    def test_http_error_is_normalized(self):
        transport = FakeTransport([(503, b'{"error":"server busy"}')])
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.kind, "http")

    def test_http_error_unwraps_openai_error_object(self):
        transport = FakeTransport(
            [(500, b'{"error":{"message":"model overloaded","type":"server_error"}}')]
        )
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.kind, "http")
        self.assertIn("model overloaded", str(ctx.exception))

    def test_loaded_models_is_unsupported_not_silent(self):
        client = OpenAICompatibleClient(openai_profile(), transport=FakeTransport([]))

        with self.assertRaises(OllamaError) as ctx:
            client.loaded_models()

        self.assertEqual(ctx.exception.kind, "unsupported")

    def test_unload_model_is_unsupported_not_silent(self):
        client = OpenAICompatibleClient(openai_profile(), transport=FakeTransport([]))

        with self.assertRaises(OllamaError) as ctx:
            client.unload_model()

        self.assertEqual(ctx.exception.kind, "unsupported")

    def test_model_not_found_404_triggers_resolve(self):
        transport = FakeTransport(
            [
                (404, b'{"error":"model \'ling-3.0-tiny-q6k\' not found"}'),
                (200, b'{"data":[{"id":"ling-3.0-tiny-q6k"}]}'),
                (
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"role": "assistant", "content": "done"}}
                            ],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    ).encode("utf-8"),
                ),
            ]
        )
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(result["message"]["content"], "done")
        self.assertEqual(client._active_model_name, "ling-3.0-tiny-q6k")
        # requests: chat(404) -> available_models -> chat(retry)
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(transport.requests[1]["path"], "/v1/models")
        retry_payload = json.loads(transport.requests[2]["body"].decode("utf-8"))
        self.assertEqual(retry_payload["model"], "ling-3.0-tiny-q6k")

    def test_model_not_found_resolves_to_matching_base(self):
        transport = FakeTransport(
            [
                (404, b'{"error":"model not found"}'),
                (200, b'{"data":[{"id":"ling-3.0-tiny-q6k"}]}'),
                (
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"role": "assistant", "content": "done"}}
                            ],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    ).encode("utf-8"),
                ),
            ]
        )
        client = OpenAICompatibleClient(
            openai_profile(model="ling-3.0-tiny-q6k.gguf"), transport=transport
        )

        client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(client._active_model_name, "ling-3.0-tiny-q6k")
        retry_payload = json.loads(transport.requests[2]["body"].decode("utf-8"))
        self.assertEqual(retry_payload["model"], "ling-3.0-tiny-q6k")

    def test_bad_request_400_without_model_word_does_not_switch(self):
        transport = FakeTransport([(400, b'{"error":"invalid request body"}')])
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.kind, "http")
        self.assertEqual(ctx.exception.status_code, 400)
        # only the original chat request; no fallback, no available_models
        self.assertEqual(len(transport.requests), 1)

    def test_ollama_error_carries_status_code(self):
        transport = FakeTransport([(500, b'{"error":"boom"}')])
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.kind, "http")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_nonfinite_timings_do_not_raise(self):
        transport = FakeTransport(
            [
                (
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"role": "assistant", "content": "done"}}
                            ],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                            "timings": {"prompt_ms": "inf", "predicted_ms": "nan"},
                        }
                    ).encode("utf-8"),
                )
            ]
        )
        client = OpenAICompatibleClient(openai_profile(), transport=transport)

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(result["eval_duration"], 0)
        self.assertEqual(result["total_duration"], 0)

    def _sse(self, *objects):
        lines = []
        for obj in objects:
            lines.append("data: " + json.dumps(obj) + "\n")
        return ("".join(lines)).encode("utf-8")

    def test_streaming_sse_accumulates_content_tool_calls_and_usage(self):
        profile = openai_profile(stream_idle_timeout_seconds=5.0)
        chunks = [
            self._sse(
                {
                    "choices": [
                        {
                            "delta": {
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
                    ]
                }
            ),
            self._sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "lo",
                                "tool_calls": [
                                    {"index": 0, "function": {"name": "", "arguments": 'th": "a.py"}'}}
                                ],
                            }
                        }
                    ]
                }
            ),
            self._sse({"choices": [{"delta": {}}]}),
            self._sse(
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                    "timings": {"prompt_ms": 12.5, "predicted_ms": 250.0},
                }
            ),
            b"data: [DONE]\n",
        ]
        transport = StreamingFakeTransport(chunks)
        client = OpenAICompatibleClient(profile, transport=transport)

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(result["message"]["content"], "Hello")
        call = result["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "read_file")
        self.assertEqual(call["function"]["arguments"], {"path": "a.py"})
        self.assertEqual(result["prompt_eval_count"], 100)
        self.assertEqual(result["eval_count"], 40)
        self.assertEqual(result["prompt_eval_duration"], 12_500_000)
        self.assertEqual(result["eval_duration"], 250_000_000)
        self.assertEqual(result["total_duration"], 262_500_000)

        method, path, body, headers, timeout = transport.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1/chat/completions")
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_streaming_without_done_raises_stream_closed(self):
        profile = openai_profile()
        chunks = [
            self._sse({"choices": [{"delta": {"content": "hi"}}]}),
        ]
        client = OpenAICompatibleClient(profile, transport=StreamingFakeTransport(chunks))

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.kind, "stream_closed")

    def test_streaming_idle_timeout_raises_timeout(self):
        profile = openai_profile(stream_idle_timeout_seconds=0.05)

        class SlowTransport:
            def __init__(self):
                self.requests = []

            def request(self, method, path, body, headers, timeout):
                self.requests.append((method, path))
                raise AssertionError("buffered path should not be used")

            def stream(self, method, path, body, headers, timeout):
                self.requests.append((method, path))
                yield (b"data: " + json.dumps({"choices": [{"delta": {"content": "a"}}]}).encode("utf-8") + b"\n")
                time.sleep(0.2)
                yield (b"data: " + json.dumps({"choices": [{"delta": {"content": "b"}}]}).encode("utf-8") + b"\n")
                yield b"data: [DONE]\n"

        client = OpenAICompatibleClient(profile, transport=SlowTransport())

        with self.assertRaises(OllamaError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_streaming_model_not_found_404_triggers_resolve(self):
        profile = openai_profile()

        class ResolveTransport:
            def __init__(self):
                self.requests = []
                self.stream_calls = 0

            def request(self, method, path, body, headers, timeout):
                self.requests.append((method, path, body, headers, timeout))
                if path == "/v1/models":
                    return 200, b'{"data":[{"id":"ling-3.0-tiny-q6k"}]}'
                raise AssertionError(f"unexpected buffered request {path}")

            def stream(self, method, path, body, headers, timeout):
                self.requests.append((method, path, body, headers, timeout))
                self.stream_calls += 1
                if self.stream_calls == 1:
                    raise OllamaError("model not found", kind="http", status_code=404)
                yield b"data: " + json.dumps(
                    {"choices": [{"delta": {"content": "done"}}]}
                ).encode("utf-8") + b"\n"
                yield b"data: [DONE]\n"

        transport = ResolveTransport()
        client = OpenAICompatibleClient(profile, transport=transport)

        result = client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(client._active_model_name, "ling-3.0-tiny-q6k")
        self.assertEqual(result["message"]["content"], "done")
        # stream(404) -> available_models -> stream(retry)
        self.assertEqual(transport.stream_calls, 2)
        models = [r for r in transport.requests if r[1] == "/v1/models"]
        self.assertEqual(len(models), 1)
        retry_body = [r for r in transport.requests if r[1] == "/v1/chat/completions"][1][2]
        self.assertEqual(json.loads(retry_body.decode("utf-8"))["model"], "ling-3.0-tiny-q6k")


class BuildClientTests(unittest.TestCase):
    def test_build_client_dispatches_openai_provider(self):
        self.assertIsInstance(build_client(openai_profile()), OpenAICompatibleClient)

    def test_build_client_defaults_to_ollama(self):
        profile = ModelProfile(
            name="small-coder",
            model="qwen2.5:1.5b",
            endpoint="http://127.0.0.1:11434",
        )
        self.assertIsInstance(build_client(profile), OllamaClient)


if __name__ == "__main__":
    unittest.main()
