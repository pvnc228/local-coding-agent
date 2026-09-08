import unittest

from local_coding_agent.profiles import get_profile, list_profiles


class ModelProfileTests(unittest.TestCase):
    def test_builtin_profiles_are_named_and_have_bounded_defaults(self):
        names = list_profiles()

        self.assertEqual(
            names,
            (
                "devstral-small-2-24b",
                "gemma4-e2b-q4",
                "gemma4-e2b-q8",
                "gemma4-e4b-q4",
                "gemma4-e4b-q5",
                "ling-3.0-tiny-q6k",
                "nemotron-30b-mxfp4",

                "ornith-9b",
                "qwen2.5-1.5b",
                "qwen2.5-coder",
                "qwen2.5-coder-14b-q6k",
                "qwen2.5-coder-7b-q4",
                "qwen2.5-coder-7b-q5",
                "qwen3-8b-q6k",
                "qwen3-coder-30b",
                "qwen3-coder-30b-iq2",
                "qwen3-coder-30b-q4",
                "qwen3.8-27b-q4",
                "qwen3.8-27b-q5",
                "qwen3.8-27b-think",
            ),
        )
        profile = get_profile("qwen2.5-coder")
        self.assertEqual(profile.model, "qwen2.5-coder:latest")
        self.assertFalse(profile.think)
        self.assertEqual(profile.temperature, 0)
        self.assertLessEqual(profile.num_ctx, 8192)
        self.assertLessEqual(profile.num_predict, 512)

        research_profile = get_profile("devstral-small-2-24b")
        self.assertEqual(research_profile.model, "codex-devstral-small-2-24b:latest")
        self.assertEqual(research_profile.num_ctx, 8192)

    def test_profile_endpoint_can_be_overridden_without_mutating_registry(self):
        profile = get_profile("qwen2.5-1.5b", endpoint="http://127.0.0.1:9999")

        self.assertEqual(profile.endpoint, "http://127.0.0.1:9999")
        self.assertEqual(get_profile("qwen2.5-1.5b").endpoint, "http://127.0.0.1:11434")

    def test_context_window_can_be_overridden_and_cannot_exceed_model_limit(self):
        profile = get_profile("qwen2.5-1.5b", num_ctx=16_384)

        self.assertEqual(profile.num_ctx, 16_384)
        with self.assertRaises(ValueError):
            get_profile("qwen2.5-1.5b", num_ctx=0)
        # The historical 32k value is a policy recommendation, not proof of
        # the installed model's runtime limit.  A verified limit is explicit.
        self.assertEqual(get_profile("qwen2.5-1.5b", num_ctx=32_769).num_ctx, 32_769)
        with self.assertRaises(ValueError):
            get_profile("qwen2.5-1.5b", num_ctx=32_769, model_context_limit=32_768)


    def test_get_profile_defaults_for_qwen3_8b_and_sampling_options(self):
        profile = get_profile("qwen3-8b-q6k")
        self.assertEqual(profile.temperature, 0.7)
        self.assertEqual(profile.top_p, 0.80)
        self.assertEqual(profile.presence_penalty, 1.5)

        custom = get_profile("qwen3-8b-q6k", top_k=40, min_p=0.05, seed=42)
        self.assertEqual(custom.top_k, 40)
        self.assertEqual(custom.min_p, 0.05)
        self.assertEqual(custom.seed, 42)

    def test_profile_system_contract_can_be_customized(self):
        profile = get_profile("qwen2.5-coder", system_contract="Custom system contract")
        self.assertEqual(profile.system_contract, "Custom system contract")

if __name__ == "__main__":
    unittest.main()
