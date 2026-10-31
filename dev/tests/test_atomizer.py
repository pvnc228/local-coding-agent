import unittest

from local_coding_agent.atomizer import (
    Decomposition,
    PreflightReport,
    TaskBudget,
    decompose,
    preflight,
)
from local_coding_agent.task import TaskEnvelope


class TaskBudgetTests(unittest.TestCase):
    def test_budget_fields_must_be_positive(self):
        with self.assertRaises(ValueError):
            TaskBudget(max_files=0)
        with self.assertRaises(ValueError):
            TaskBudget(max_context_bytes=0)
        with self.assertRaises(ValueError):
            TaskBudget(max_checks=0)
        with self.assertRaises(ValueError):
            TaskBudget(max_files=-1)

    def test_budget_fields_are_independently_settable(self):
        budget = TaskBudget(max_files=2, max_context_bytes=10, max_checks=1)

        self.assertEqual(budget.max_files, 2)
        self.assertEqual(budget.max_context_bytes, 10)
        self.assertEqual(budget.max_checks, 1)


class PreflightTests(unittest.TestCase):
    def test_accepts_task_within_budget(self):
        task = TaskEnvelope(id="task-1", goal="read", files=("a.py", "b.py"))
        report = preflight(task, TaskBudget())

        self.assertTrue(report.accepted)
        self.assertIsNone(report.reason)
        self.assertEqual(report.issues, ())

    def test_rejects_too_many_files(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"),
        )
        report = preflight(task, TaskBudget(max_files=5))

        self.assertFalse(report.accepted)
        self.assertEqual(report.reason, "too_many_files")
        self.assertNotEqual(report.issues, ())

    def test_rejects_context_too_large(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py",),
            context="x" * 33,
        )
        report = preflight(task, TaskBudget(max_context_bytes=32))

        self.assertFalse(report.accepted)
        self.assertEqual(report.reason, "context_too_large")
        self.assertNotEqual(report.issues, ())

    def test_rejects_too_many_checks(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py",),
            checks=("check-1", "check-2", "check-3", "check-4"),
        )
        report = preflight(task, TaskBudget(max_checks=3))

        self.assertFalse(report.accepted)
        self.assertEqual(report.reason, "too_many_checks")
        self.assertNotEqual(report.issues, ())


class DecomposeTests(unittest.TestCase):
    def test_identity_decomposition_when_task_fits(self):
        task = TaskEnvelope(id="task-1", goal="read", files=("a.py", "b.py"))
        decomposition = decompose(task, TaskBudget(max_files=5))

        self.assertEqual(decomposition.children, (task,))

    def test_splits_overwide_task_into_contiguous_bounded_children(self):
        files = ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py")
        task = TaskEnvelope(
            id="wide-task",
            goal="read all",
            files=files,
            context="some context",
            constraints=("keep",),
            checks=("check",),
            acceptance=("accept",),
        )
        decomposition = decompose(task, TaskBudget(max_files=3))

        self.assertEqual(len(decomposition.children), 3)

        first, second, third = decomposition.children
        self.assertEqual(first.files, ("a.py", "b.py", "c.py"))
        self.assertEqual(second.files, ("d.py", "e.py", "f.py"))
        self.assertEqual(third.files, ("g.py",))

        for child in decomposition.children:
            self.assertLessEqual(len(child.files), 3)
            self.assertTrue(set(child.files).issubset(set(files)))

        self.assertEqual(first.id, "wide-task#1")
        self.assertEqual(second.id, "wide-task#2")
        self.assertEqual(third.id, "wide-task#3")

        for child in decomposition.children:
            self.assertEqual(child.goal, "read all")
            self.assertEqual(child.context, "some context")
            self.assertEqual(child.constraints, ("keep",))
            self.assertEqual(child.checks, ("check",))
            self.assertEqual(child.acceptance, ("accept",))

    def test_raises_when_too_many_checks(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py",),
            checks=("check-1", "check-2", "check-3", "check-4"),
        )
        with self.assertRaises(ValueError) as context:
            decompose(task, TaskBudget(max_checks=3))
        self.assertIn("too_many_checks", str(context.exception))

    def test_raises_when_context_too_large(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py",),
            context="x" * 33,
        )
        with self.assertRaises(ValueError) as context:
            decompose(task, TaskBudget(max_context_bytes=32))
        self.assertIn("context_too_large", str(context.exception))

    def test_raises_when_files_and_context_over_budget(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"),
            context="x" * 33,
        )
        with self.assertRaises(ValueError) as context:
            decompose(task, TaskBudget(max_files=3, max_context_bytes=32))
        self.assertIn("context_too_large", str(context.exception))

    def test_raises_when_files_and_checks_over_budget(self):
        task = TaskEnvelope(
            id="task-1",
            goal="read",
            files=("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"),
            checks=("check-1", "check-2", "check-3", "check-4"),
        )
        with self.assertRaises(ValueError) as context:
            decompose(task, TaskBudget(max_files=3, max_checks=3))
        self.assertIn("too_many_checks", str(context.exception))

    def test_exact_division_yields_two_children(self):
        files = ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py")
        task = TaskEnvelope(id="task-1", goal="read", files=files)
        decomposition = decompose(task, TaskBudget(max_files=3))

        self.assertEqual(len(decomposition.children), 2)
        first, second = decomposition.children
        self.assertEqual(first.files, ("a.py", "b.py", "c.py"))
        self.assertEqual(second.files, ("d.py", "e.py", "f.py"))

    def test_children_have_disjoint_files(self):
        files = ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py")
        task = TaskEnvelope(id="task-1", goal="read", files=files)
        decomposition = decompose(task, TaskBudget(max_files=3))

        self.assertEqual(len(decomposition.children), 3)
        first, second, third = decomposition.children
        self.assertEqual(set(first.files) & set(second.files), set())
        self.assertEqual(set(first.files) & set(third.files), set())
        self.assertEqual(set(second.files) & set(third.files), set())
        self.assertEqual(set(first.files), {"a.py", "b.py", "c.py"})
        self.assertEqual(set(second.files), {"d.py", "e.py", "f.py"})
        self.assertEqual(set(third.files), {"g.py"})


if __name__ == "__main__":
    unittest.main()
