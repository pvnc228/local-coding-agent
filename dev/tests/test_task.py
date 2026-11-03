import unittest

from local_coding_agent.task import TaskEnvelope


class TaskEnvelopeTests(unittest.TestCase):
    def test_task_copies_mutable_collections_into_immutable_tuples(self):
        files = ["src/file.py"]
        checks = ["py -m unittest"]
        task = TaskEnvelope(id="stable", goal="read", files=files, checks=checks)

        files.append("secret.txt")
        checks.append("dangerous command")

        self.assertEqual(task.files, ("src/file.py",))
        self.assertEqual(task.checks, ("py -m unittest",))

    def test_task_rejects_absolute_and_parent_paths_in_allowlist(self):
        with self.assertRaises(ValueError):
            TaskEnvelope(id="bad", goal="read", files=("../secret.txt",))

        with self.assertRaises(ValueError):
            TaskEnvelope(id="bad", goal="read", files=("C:/secret.txt",))

        with self.assertRaises(ValueError):
            TaskEnvelope(id="bad", goal="read", files="src/file.py")


if __name__ == "__main__":
    unittest.main()
