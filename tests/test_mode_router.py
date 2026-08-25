"""Unit tests for Mode Router: fast heuristic classifier and hybrid mode routing."""

import pytest
from local_coding_agent.mode_router import (
    DEFAULT_MODE_ROUTER_PROFILE,
    MODES,
    VALID_ROUTED,
    ModeName,
    build_mode_router,
    classify_fast,
    classify_mode,
)


class _FakeClient:
    def __init__(self, content=None, exc=None, payload=None):
        self.content = content
        self.exc = exc
        self.payload = payload
        self.called_with = None

    def chat(self, messages):
        self.called_with = messages
        if self.exc is not None:
            raise self.exc
        if self.payload is not None:
            return self.payload
        return {"message": {"content": self.content}}


class TestClassifyFast:
    def test_empty_none_and_whitespace_is_chat(self):
        assert classify_fast("") == "chat"
        assert classify_fast("   ") == "chat"
        assert classify_fast(None) == "chat"

    @pytest.mark.parametrize(
        "greeting",
        ["hi", "hello", "hey", "привет", "здравствуйте", "yo", "sup",
         "thanks", "спасибо", "ok", "help", "test"],
    )
    def test_each_greeting_is_chat(self, greeting):
        assert classify_fast(greeting) == "chat"

    @pytest.mark.parametrize(
        "phrase",
        ["how are you", "what's up", "whats up", "wassup", "how are u"],
    )
    def test_small_talk_phrases_are_chat(self, phrase):
        assert classify_fast(phrase) == "chat"

    def test_planning_intent(self):
        assert classify_fast("plan: add feature x") == "plan"
        assert classify_fast("plan the migration") == "plan"
        assert classify_fast("спланируй задачу") == "plan"
        assert classify_fast("это план") == "plan"
        assert classify_fast("составь план для проекта") == "plan"
        assert classify_fast("спланируй: рефакторинг") == "plan"

    def test_plan_word_boundary_avoids_false_positives(self):
        assert classify_fast("fix the planner") == "build"
        assert classify_fast("airplane seat") == "build"
        assert classify_fast("запланировать встречу") == "build"

    def test_info_prefix_wins_over_plan_keyword(self):
        assert classify_fast("explain the deployment plan") == "chat"

    def test_informational_inquiry_is_chat(self):
        assert classify_fast("read main.py") == "chat"
        assert classify_fast("explain foo") == "chat"
        assert classify_fast("how does x work") == "chat"
        assert classify_fast("что делает main.py") == "chat"
        assert classify_fast("show me the config") == "chat"
        assert classify_fast("опиши алгоритм") == "chat"

    def test_embedded_question_is_chat(self):
        # Questions buried after a conversational lead-in, not just prefixes.
        assert classify_fast("can u tell me what main.py does?") == "chat"
        assert classify_fast(
            "i want to chat about the app, can u tell me where main.py is and what it does&"
        ) == "chat"
        assert classify_fast("go to left panel component in the desktop app") == "chat"
        assert classify_fast("where is the config loaded?") == "chat"

    def test_build_verb_overrides_question_wording(self):
        assert classify_fast("fix how the retry loop counts turns") == "build"
        assert classify_fast("write unit tests that show progress") == "build"
        assert classify_fast("fix the off-by-one in window.py?") == "build"

    def test_build_default(self):
        assert classify_fast("fix off-by-one in sliding window") == "build"
        assert classify_fast("write unit tests for tax calculation") == "build"

    def test_never_returns_hybrid(self):
        for p in ["", "hello", "plan: x", "read x", "fix the bug"]:
            assert classify_fast(p) != "hybrid"


class TestClassifyModeFastPath:
    def test_router_none_always_uses_fast_path(self):
        for counter in (0, 1, 2, 5):
            assert classify_mode("fix the bug", counter=counter) == "build"
            assert classify_mode("plan: add x", counter=counter) == "plan"
            assert classify_mode("hello", counter=counter) == "chat"

    def test_router_none_ignores_n_every(self):
        assert classify_mode("read main.py", n_every=1, counter=0) == "chat"

    def test_non_callable_router_treated_as_no_router(self):
        assert classify_mode("fix the bug", router="not-callable") == "build"
        assert classify_mode("plan: add x", router=12345) == "plan"


class TestClassifyModeHybridPath:
    def test_valid_router_result_honored_when_counter_hits(self):
        router = lambda recent: "plan"  # noqa: E731
        assert classify_mode("hello", router=router, n_every=3, counter=0) == "plan"
        # counter=1 -> 1 % 3 != 0 -> fast path (chat for greeting)
        assert classify_mode("hello", router=router, n_every=3, counter=1) == "chat"

    def test_router_none_falls_back_to_fast_path(self):
        router = lambda recent: None  # noqa: E731
        assert classify_mode("hello", router=router, n_every=3, counter=0) == "chat"
        assert classify_mode("plan: add x", router=router, n_every=3, counter=0) == "plan"

    def test_router_raising_falls_back_to_fast_path(self):
        def router(recent):
            raise RuntimeError("model failure")
        assert classify_mode("hello", router=router, n_every=3, counter=0) == "chat"
        assert classify_mode("fix the bug", router=router, n_every=3, counter=0) == "build"

    def test_recent_prompts_passed_through(self):
        seen = {}

        def router(recent):
            seen["recent"] = recent
            return "build"

        classify_mode("hello", router=router, n_every=1, counter=0,
                      recent_prompts=["hello", "fix x"])
        assert seen["recent"] == ["hello", "fix x"]

        # None recent passes through as None
        classify_mode("hello", router=router, n_every=1, counter=0)
        assert seen["recent"] is None


class TestClassifyModeClamping:
    def test_n_every_clamped_to_1(self):
        assert classify_mode("hello", n_every=0, counter=0) == "chat"
        assert classify_mode("hello", n_every=-5, counter=0) == "chat"

    def test_counter_clamped_to_0(self):
        assert classify_mode("hello", n_every=3, counter=-2) == "chat"
        assert classify_mode("plan: add x", n_every=3, counter=-1) == "plan"

    def test_never_returns_hybrid(self):
        router = lambda recent: "hybrid"  # noqa: E731
        for p in ["hello", "plan: x", "read x", "fix the bug"]:
            assert classify_mode(p, router=router, n_every=1, counter=0) != "hybrid"


class TestAdversarialModeRouter:
    def test_invalid_router_strings_fall_back(self):
        for bad in ("HYBRID", "hybrid", "evil", "build!", "Plan"):
            router = lambda recent, _b=bad: _b  # noqa: E731
            assert classify_mode("hello", router=router, n_every=1, counter=0) == "chat"

    def test_valid_routed_constant_matches_modes(self):
        assert set(VALID_ROUTED) == {"chat", "build", "plan"}

    def test_modes_tuple_contains_hybrid_but_routed_does_not(self):
        assert "hybrid" in MODES
        assert "hybrid" not in VALID_ROUTED
        assert ModeName.HYBRID.value == "hybrid"


class TestModeRouter:
    def test_returns_build(self):
        client = _FakeClient(content="build")
        router = build_mode_router(client=client)
        assert router(["fix the bug"]) == "build"

    def test_returns_plan(self):
        client = _FakeClient(content="plan")
        router = build_mode_router(client=client)
        assert router(["make a plan"]) == "plan"

    def test_returns_chat(self):
        client = _FakeClient(content="chat")
        router = build_mode_router(client=client)
        assert router(["hello there"]) == "chat"

    @pytest.mark.parametrize("bad", ["yes", "HYBRID", "unknown"])
    def test_unroutable_content_returns_none(self, bad):
        client = _FakeClient(content=bad)
        router = build_mode_router(client=client)
        assert router(["hello"]) is None

    def test_punctuated_routed_word_normalized(self):
        client = _FakeClient(content="build!")
        router = build_mode_router(client=client)
        assert router(["hello"]) == "build"

    def test_client_raising_returns_none(self):
        client = _FakeClient(exc=RuntimeError("boom"))
        router = build_mode_router(client=client)
        assert router(["hello"]) is None

    def test_empty_payload_returns_none(self):
        client = _FakeClient(payload={})
        router = build_mode_router(client=client)
        assert router(["hello"]) is None

    def test_missing_message_returns_none(self):
        client = _FakeClient(payload={"message": {}})
        router = build_mode_router(client=client)
        assert router(["hello"]) is None

    def test_none_recent_prompts_ok(self):
        client = _FakeClient(content="build")
        router = build_mode_router(client=client)
        assert router(None) == "build"
        assert router() == "build"

    def test_lazy_import_builds_real_client(self, monkeypatch):
        fake_client = _FakeClient(content="plan")
        calls = {}

        def fake_build_client(profile):
            calls["profile"] = profile
            return fake_client

        def fake_get_profile(name, **overrides):
            calls["name"] = name
            return "profile-object"

        monkeypatch.setattr(
            "local_coding_agent.ollama_adapter.build_client", fake_build_client
        )
        monkeypatch.setattr("local_coding_agent.profiles.get_profile", fake_get_profile)

        router = build_mode_router()
        assert router(["hi"]) == "plan"
        assert calls["name"] == DEFAULT_MODE_ROUTER_PROFILE
        assert calls["profile"] == "profile-object"

    def test_recent_prompts_passed_as_context(self):
        client = _FakeClient(content="chat")
        router = build_mode_router(client=client)
        router(["first", "second"])
        messages = client.called_with
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "- first" in user and "- second" in user


class TestModeRouterAdversarial:
    def test_whitespace_and_case_normalized(self):
        client = _FakeClient(content="  BUILD  ")
        router = build_mode_router(client=client)
        assert router(["fix x"]) == "build"

    def test_trailing_punctuation_stripped(self):
        client = _FakeClient(content="  PLAN.  ")
        router = build_mode_router(client=client)
        assert router(["make a plan"]) == "plan"

    def test_default_profile_constant(self):
        assert DEFAULT_MODE_ROUTER_PROFILE == "qwen2.5-1.5b"
