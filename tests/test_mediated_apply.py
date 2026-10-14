"""Red tests for the controller-owned mediated apply seam (v1.1 step 1).

`mediated_apply` is the single shared implementation behind the service, CLI
and desktop apply paths. These tests pin its contract with real git workspaces
and real subprocess checks, so no self-reported model evidence is trusted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch as mock_patch

from local_coding_agent.controller import mediated_apply
from local_coding_agent.task import TaskEnvelope

PASS_CHECK = 'python -c "raise SystemExit(0)"'
FAIL_CHECK = 'python -c "raise SystemExit(1)"'


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=False)
    subprocess.run(["git", "add", "."], cwd=path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=path,
        check=False,
    )


def _modify_patch(name: str, old: str, new: str) -> str:
    return (
        f"diff --git a/{name} b/{name}\n"
        f"--- a/{name}\n"
        f"+++ b/{name}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def _create_patch(name: str, content: str) -> str:
    return (
        f"diff --git a/{name} b/{name}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{name}\n"
        "@@ -0,0 +1 @@\n"
        f"+{content}\n"
    )


def _task(files: tuple[str, ...], checks: tuple[str, ...]) -> TaskEnvelope:
    return TaskEnvelope(id="t", goal="apply reviewed patch", files=files, checks=checks)


def test_rejects_empty_checks(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _modify_patch("a.py", "VALUE = 1", "VALUE = 2")

    result = mediated_apply(tmp_path, patch, _task(("a.py",), ()))

    assert result["status"] != "applied"
    assert result["status"] == "failed"
    assert result["error"]["kind"] == "apply_requires_checks"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rejects_out_of_scope_patch(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("OTHER = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _modify_patch("b.py", "OTHER = 1", "OTHER = 2")

    result = mediated_apply(tmp_path, patch, _task(("a.py",), (PASS_CHECK,)))

    assert result["status"] == "failed"
    assert result["error"]["kind"] == "patch_outside_scope"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "OTHER = 1\n"


def test_applies_when_checks_pass(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _modify_patch("a.py", "VALUE = 1", "VALUE = 2")

    result = mediated_apply(tmp_path, patch, _task(("a.py",), (PASS_CHECK,)))

    assert result.get("applied") is True
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    observed = result["checks"][0]
    assert observed["command"] == PASS_CHECK
    assert observed["passed"] is True
    assert observed["exit_code"] == 0
    assert "stdout" in observed and "stderr" in observed


def test_rolls_back_when_check_fails(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _modify_patch("a.py", "VALUE = 1", "VALUE = 2")

    result = mediated_apply(tmp_path, patch, _task(("a.py",), (FAIL_CHECK,)))

    assert result["status"] == "rejected"
    assert result["error"]["kind"] == "post_apply_check_failed"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rollback_removes_created_file(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _create_patch("new.py", "CREATED = 1")

    result = mediated_apply(tmp_path, patch, _task(("new.py",), (FAIL_CHECK,)))

    assert result["status"] == "rejected"
    assert not (tmp_path / "new.py").exists()


def test_detects_unverified_rollback(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_repo(tmp_path)
    patch = _modify_patch("a.py", "VALUE = 1", "VALUE = 2")

    from local_coding_agent.controller import _post_apply

    real_apply_patch = _post_apply.apply_patch

    def apply_then_skip_reverse(root, candidate, *, reverse=False):
        if reverse:
            return True, ""
        return real_apply_patch(root, candidate)

    with mock_patch.object(_post_apply, "apply_patch", side_effect=apply_then_skip_reverse):
        result = mediated_apply(tmp_path, patch, _task(("a.py",), (FAIL_CHECK,)))

    assert result["workspace_modified"] is True
    assert result["workspace_state"] == "unknown"
    assert any(risk["kind"] == "rollback_failed" for risk in result["risks"])
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VALUE = 2\n"
