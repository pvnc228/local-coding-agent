import hashlib
import tempfile
import unittest
from pathlib import Path

from local_coding_agent.observation_policy import (
    FsObservationError,
    FsObservationGate,
    is_observed,
    observe_file,
    reset_session,
    verify_edit_intent,
)


class ObservationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.gate = FsObservationGate()

        self.sample_file = self.workspace / "sample.py"
        self.sample_file.write_text("print('hello world')\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_observe_file_explicit_str_content(self):
        content = "x = 42\n"
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        h = self.gate.observe_file(
            session_id="s1",
            file_path="src/module.py",
            content=content,
        )
        self.assertEqual(h, expected_hash)
        self.assertTrue(self.gate.is_observed("s1", "src/module.py"))
        self.assertEqual(self.gate.get_observed_hash("s1", "src/module.py"), expected_hash)

    def test_observe_file_explicit_bytes_content(self):
        raw_bytes = b"binary data \x01\x02"
        expected_hash = hashlib.sha256(raw_bytes).hexdigest()

        h = self.gate.observe_file(
            session_id="s1",
            file_path="data.bin",
            content=raw_bytes,
        )
        self.assertEqual(h, expected_hash)
        self.assertTrue(self.gate.is_observed("s1", "data.bin"))

    def test_observe_file_reads_from_disk(self):
        expected_hash = hashlib.sha256(self.sample_file.read_bytes()).hexdigest()

        h = self.gate.observe_file(
            session_id="s1",
            file_path=self.sample_file,
        )
        self.assertEqual(h, expected_hash)
        self.assertTrue(self.gate.is_observed("s1", self.sample_file))

    def test_observe_nonexistent_file_without_content_raises(self):
        ghost_path = self.workspace / "does_not_exist.py"
        with self.assertRaises(FileNotFoundError):
            self.gate.observe_file(session_id="s1", file_path=ghost_path)

    def test_verify_edit_intent_unobserved_and_observed(self):
        file_path = "src/app.py"

        # Initially unobserved
        allowed, reason = self.gate.verify_edit_intent("session-1", file_path)
        self.assertFalse(allowed)
        self.assertEqual(
            reason,
            f"FS_NOT_OBSERVED: edit requires reading '{file_path}' first",
        )

        # Observe the file
        self.gate.observe_file("session-1", file_path, content="# code")

        # Now verified
        allowed, reason = self.gate.verify_edit_intent("session-1", file_path)
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_verify_freshness(self):
        session_id = "s_fresh"
        self.gate.observe_file(session_id, self.sample_file)

        # Fresh file check passes
        fresh, err = self.gate.verify_freshness(session_id, self.sample_file)
        self.assertTrue(fresh)
        self.assertIsNone(err)

        # Modify file on disk
        self.sample_file.write_text("print('modified!')\n", encoding="utf-8")

        # Freshness check detects external mutation
        fresh, err = self.gate.verify_freshness(session_id, self.sample_file)
        self.assertFalse(fresh)
        self.assertIn("FS_STALE", err)

        # Delete file on disk
        self.sample_file.unlink()
        fresh, err = self.gate.verify_freshness(session_id, self.sample_file)
        self.assertFalse(fresh)
        self.assertIn("FS_DELETED", err)

    def test_session_isolation_and_reset(self):
        self.gate.observe_file("session-A", "file.py", content="A")
        self.assertTrue(self.gate.is_observed("session-A", "file.py"))
        self.assertFalse(self.gate.is_observed("session-B", "file.py"))

        # Reset session A
        self.gate.reset_session("session-A")
        self.assertFalse(self.gate.is_observed("session-A", "file.py"))

    def test_canonical_path_normalization(self):
        # Relative path vs Absolute path of same file
        rel_path = Path("sample.py")
        abs_path = self.sample_file.resolve()

        self.gate.observe_file("s1", abs_path)
        # Querying with abs_path works
        self.assertTrue(self.gate.is_observed("s1", abs_path))

    def test_module_level_convenience_helpers(self):
        sess = "default-gate-session"
        fpath = "utils/math.py"

        self.assertFalse(is_observed(sess, fpath))
        allowed, reason = verify_edit_intent(sess, fpath)
        self.assertFalse(allowed)

        observe_file(sess, fpath, content="def add(a, b): return a + b")
        self.assertTrue(is_observed(sess, fpath))

        allowed, reason = verify_edit_intent(sess, fpath)
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        reset_session(sess)
        self.assertFalse(is_observed(sess, fpath))

    def test_fs_observation_error_type(self):
        err = FsObservationError("Observation check failed")
        self.assertIsInstance(err, Exception)


if __name__ == "__main__":
    unittest.main()
