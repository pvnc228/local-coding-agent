import os
import tempfile
import unittest
from pathlib import Path

from local_coding_agent.spill import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    SpillRef,
    SpillStore,
    maybe_spill,
    read_spill,
    save_text,
)


class SpillStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        self.store = SpillStore(root_dir=self.root_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_text_creates_file_and_returns_ref(self):
        content = "Line 1: Hello\nLine 2: World\nLine 3: Test\n"
        ref = self.store.save_text(
            session_id="session-123",
            content=content,
            source_tool="run_command",
            suggested_name="cmd_out.txt",
        )

        self.assertIsInstance(ref, SpillRef)
        self.assertTrue(ref.locator.endswith("cmd_out.txt"))
        self.assertTrue(Path(ref.locator).exists())
        self.assertEqual(ref.bytes, len(content.encode("utf-8")))
        self.assertEqual(ref.lines, 3)
        self.assertIn("Line 1: Hello", ref.preview_head)
        self.assertIn("Line 3: Test", ref.preview_tail)
        self.assertIn(ref.locator, ref.retrieval_hint)

    def test_read_spill_full_and_sliced(self):
        lines = [f"Item {i}\n" for i in range(100)]
        content = "".join(lines)
        ref = self.store.save_text("s1", content)

        # Full read
        full_read = self.store.read_spill(ref.locator, offset_line=0, limit_lines=200)
        self.assertEqual(full_read, content)

        # Slice read
        slice_read = self.store.read_spill(ref.locator, offset_line=10, limit_lines=5)
        expected_slice = "".join(lines[10:15])
        self.assertEqual(slice_read, expected_slice)

        # Negative offset clamped to 0
        clamped_read = self.store.read_spill(ref.locator, offset_line=-5, limit_lines=2)
        self.assertEqual(clamped_read, "".join(lines[0:2]))

    def test_read_spill_nonexistent_raises_file_not_found(self):
        dummy_path = (self.root_dir / "session_s1" / "nonexistent.txt").as_posix()
        with self.assertRaises(FileNotFoundError):
            self.store.read_spill(dummy_path)

    def test_path_traversal_in_read_spill_rejected(self):
        # File outside root
        outside_file = Path(self.temp_dir.name).parent / "outside.txt"
        outside_file.write_text("secret", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                self.store.read_spill(outside_file.as_posix())
        finally:
            if outside_file.exists():
                outside_file.unlink()

    def test_path_traversal_in_session_id_sanitized(self):
        malicious_session = "../../etc/shadow"
        ref = self.store.save_text(session_id=malicious_session, content="safe")
        # Compare against the store's own resolved root: the raw temp dir is a
        # symlink on macOS (/var -> /private/var) and an 8.3 short path on
        # some Windows setups, so unresolved text comparison would be flaky.
        target_path = Path(ref.locator).resolve()
        self.assertTrue(target_path.is_relative_to(self.store.root_dir))

    def test_maybe_spill_under_limit_does_not_spill(self):
        small_content = "Small tool output\nAll good.\n"
        spilled, output, ref = self.store.maybe_spill(
            session_id="test-session",
            content=small_content,
            source_tool="web_fetch",
            max_bytes=1000,
            max_lines=10,
        )

        self.assertFalse(spilled)
        self.assertEqual(output, small_content)
        self.assertIsNone(ref)

    def test_maybe_spill_over_bytes_threshold(self):
        large_content = "x" * 200
        spilled, output, ref = self.store.maybe_spill(
            session_id="test-session",
            content=large_content,
            source_tool="run_bash",
            max_bytes=100,
            max_lines=50,
        )

        self.assertTrue(spilled)
        self.assertIsNotNone(ref)
        self.assertIn("[OUTPUT TRUNCATED & SPILLED TO STORE]", output)
        self.assertIn("run_bash", output)
        self.assertIn("exceeding limits", output)
        self.assertIn(ref.locator, output)
        self.assertEqual(self.store.read_spill(ref.locator), large_content)

    def test_maybe_spill_over_lines_threshold(self):
        many_lines = "\n".join([f"line-{i}" for i in range(25)])
        spilled, output, ref = self.store.maybe_spill(
            session_id="test-session",
            content=many_lines,
            source_tool="grep_search",
            max_bytes=100000,
            max_lines=10,
        )

        self.assertTrue(spilled)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.lines, 25)
        self.assertIn("line-0", ref.preview_head)
        self.assertIn("line-24", ref.preview_tail)

    def test_utf8_multibyte_characters(self):
        content = "Тестирование кириллицы 🚀 \nВторая строка с эмодзи 🎉\n"
        ref = self.store.save_text("utf8-session", content)
        read_back = self.store.read_spill(ref.locator)
        self.assertEqual(read_back, content)
        self.assertEqual(ref.bytes, len(content.encode("utf-8")))

    def test_module_level_helpers(self):
        content = "Global helper test\n"
        ref = save_text("global-session", content)
        self.assertTrue(Path(ref.locator).exists())

        read_content = read_spill(ref.locator)
        self.assertEqual(read_content, content)

        spilled, msg, maybe_ref = maybe_spill("global-session", "short", max_bytes=1000)
        self.assertFalse(spilled)
        self.assertEqual(msg, "short")
        self.assertIsNone(maybe_ref)


if __name__ == "__main__":
    unittest.main()
