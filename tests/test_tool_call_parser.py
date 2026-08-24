"""Tests for local_coding_agent.tool_call_parser."""

import unittest

from local_coding_agent.tool_call_parser import extract_tool_calls


class ToolCallParserTests(unittest.TestCase):
    def test_qwen_tool_call_json(self):
        text = (
            "Let me read it.\n"
            "<tool_call>{\"name\": \"read_file\", \"arguments\": {\"path\": \"src/a.py\"}}</tool_call>"
        )
        result = extract_tool_calls(text)
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0]["function"]["name"], "read_file")
        self.assertEqual(result.calls[0]["function"]["arguments"], {"path": "src/a.py"})
        self.assertNotIn("<tool_call>", result.remaining_text)
        self.assertEqual(result.formats_detected, ["<tool_call>"])

    def test_qwen_xml(self):
        text = (
            "Searching now "
            "<function=search_text><parameter=query>foo</parameter>"
            "<parameter=paths>[\"a.py\"]</parameter></function>"
        )
        result = extract_tool_calls(text)
        self.assertEqual(len(result.calls), 1)
        call = result.calls[0]["function"]
        self.assertEqual(call["name"], "search_text")
        # value parsed as JSON where possible
        self.assertEqual(call["arguments"]["query"], "foo")
        self.assertEqual(call["arguments"]["paths"], ["a.py"])

    def test_qwen_xml_spacey_parameter_variant(self):
        text = '<function=search_text><parameter name="query">bar</parameter></function>'
        result = extract_tool_calls(text)
        self.assertEqual(result.calls[0]["function"]["arguments"], {"query": "bar"})

    def test_qwen_xml_missing_function_skipped(self):
        text = '<function=><parameter=query>foo</parameter></function> plain'
        result = extract_tool_calls(text)
        self.assertEqual(result.calls, [])

    def test_llama_python_tag(self):
        text = (
            '<|python_tag|>{"name": "grep", "arguments": {"pattern": "x", "paths": ["a.py"]}}'
        )
        result = extract_tool_calls(text)
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0]["function"]["name"], "grep")
        self.assertEqual(result.calls[0]["function"]["arguments"]["paths"], ["a.py"])

    def test_mistral_tool_calls_multiple(self):
        text = (
            "[TOOL_CALLS] [{\"name\": \"read_file\", \"arguments\": {\"path\": \"a.py\"}}, "
            "{\"name\": \"grep\", \"arguments\": {\"pattern\": \"x\"}}]"
        )
        result = extract_tool_calls(text)
        self.assertEqual(len(result.calls), 2)
        self.assertEqual([c["function"]["name"] for c in result.calls], ["read_file", "grep"])
        self.assertNotIn("[TOOL_CALLS]", result.remaining_text)

    def test_allowed_names_promotes_only_allowlisted(self):
        text = (
            "<tool_call>{\"name\": \"read_file\", \"arguments\": {\"path\": \"a.py\"}}</tool_call> "
            "<tool_call>{\"name\": \"write_file\", \"arguments\": {\"path\": \"b.py\"}}</tool_call>"
        )
        result = extract_tool_calls(text, allowed_names=["read_file"])
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0]["function"]["name"], "read_file")
        # non-allowlisted call left untouched in remaining text
        self.assertIn("write_file", result.remaining_text)
        self.assertIn("<tool_call>", result.remaining_text)
        self.assertNotIn("read_file", result.remaining_text)

    def test_multiple_formats_in_one_text(self):
        text = (
            "<tool_call>{\"name\": \"read_file\", \"arguments\": {\"path\": \"a.py\"}}</tool_call> "
            "<|python_tag|>{\"name\": \"grep\", \"arguments\": {\"pattern\": \"x\"}}"
        )
        result = extract_tool_calls(text)
        self.assertEqual(len(result.calls), 2)
        self.assertEqual(sorted(result.formats_detected), ["<tool_call>", "llama_python_tag"])

    def test_truncated_json_never_raises(self):
        text = '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"'
        result = extract_tool_calls(text)
        self.assertEqual(result.calls, [])

    def test_garbage_input_never_raises(self):
        self.assertEqual(extract_tool_calls("no tool calls here").calls, [])
        self.assertEqual(extract_tool_calls("").calls, [])
        self.assertEqual(extract_tool_calls(None).calls, [])

    def test_empty_mistral_array_never_raises(self):
        self.assertEqual(extract_tool_calls("[TOOL_CALLS] []").calls, [])
        self.assertEqual(extract_tool_calls("[TOOL_CALLS] [ ]").calls, [])

    def test_arguments_must_be_dict(self):
        text = '<tool_call>{"name": "read_file", "arguments": "nope"}</tool_call>'
        result = extract_tool_calls(text)
        self.assertEqual(result.calls, [])

    def test_missing_name_dropped(self):
        text = '<tool_call>{"arguments": {"path": "a.py"}}</tool_call>'
        result = extract_tool_calls(text)
        self.assertEqual(result.calls, [])

    def test_calls_shape_matches_decode_shape(self):
        text = '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>'
        result = extract_tool_calls(text)
        call = result.calls[0]
        self.assertIn("function", call)
        self.assertIn("name", call["function"])
        self.assertIn("arguments", call["function"])


if __name__ == "__main__":
    unittest.main()
