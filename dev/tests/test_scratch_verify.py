"""Real subprocess evidence must come from patched bytes in a private copy."""
import hashlib
from pathlib import Path
from threading import Event

import pytest

from local_coding_agent.controller._scratch import scratch_workspace, verify_in_scratch
from local_coding_agent.task import TaskEnvelope


PATCH = '--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n'


def fixture(root, command='python -c "from value import VALUE; assert VALUE == 2"'):
    (root / 'value.py').write_bytes(b'VALUE = 1\r\n')
    return TaskEnvelope(id='scratch', goal='change value', files=('value.py',), checks=(command,))


def digest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('command,state', [
    ('python -c "from value import VALUE; assert VALUE == 2"', 'passed'),
    ('python -c "print(123); raise SystemExit(7)"', 'failed'),
])
def test_scratch_runs_patched_bytes_and_preserves_original(tmp_path, command, state):
    task = fixture(tmp_path, command)
    before = digest(tmp_path)
    audit = []
    verdict = verify_in_scratch(tmp_path, PATCH, task, cancel_event=None, audit=audit)
    assert verdict.state == state
    assert verdict.passed == (state == 'passed')
    assert verdict.checks[0]['exit_code'] == (0 if state == 'passed' else 7)
    assert digest(tmp_path) == before
    assert any(e['event'] == 'scratch_cleanup' for e in audit)


def test_bad_patch_and_no_checks_are_not_verified(tmp_path):
    task = fixture(tmp_path)
    assert verify_in_scratch(tmp_path, PATCH.replace('VALUE = 1', 'VALUE = 9'), task).state == 'apply_failed'
    empty = TaskEnvelope(id='x', goal='x', files=task.files)
    assert verify_in_scratch(tmp_path, PATCH, empty).state == 'unavailable'


def test_copy_limit_is_explicit_and_cleanup_always_runs(tmp_path):
    fixture(tmp_path)
    audit = []
    with pytest.raises(ValueError, match='limit'):
        with scratch_workspace(tmp_path, max_bytes=1, audit=audit):
            pytest.fail('copy should exceed limit')
    assert audit[-1]['event'] == 'scratch_cleanup'


def test_cancelled_copy_does_not_leave_temp_directory(tmp_path):
    task = fixture(tmp_path)
    event = Event(); event.set()
    result = verify_in_scratch(tmp_path, PATCH, task, cancel_event=event)
    assert result.state == 'cancelled'


def test_pythonpath_prefers_copy_over_parent_checkout(tmp_path, monkeypatch):
    task = fixture(tmp_path)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    assert verify_in_scratch(tmp_path, PATCH, task).passed


def test_copy_context_cleans_up_and_copies_bytes(tmp_path):
    fixture(tmp_path)
    with scratch_workspace(tmp_path) as copy:
        assert (copy / 'value.py').read_bytes() == b'VALUE = 1\r\n'
        assert copy != tmp_path
    assert not copy.exists()


def test_outside_scope_patch_is_rejected(tmp_path):
    task = fixture(tmp_path)
    (tmp_path / 'other.py').write_text('VALUE = 1\n')
    assert verify_in_scratch(tmp_path, PATCH.replace('value.py', 'other.py'), task).state == 'apply_failed'


def test_symlink_to_original_is_never_followed(tmp_path):
    task = fixture(tmp_path)
    try:
        (tmp_path / 'link').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    verdict = verify_in_scratch(tmp_path, PATCH, task)
    assert verdict.state == 'unavailable'
    assert (tmp_path / 'value.py').read_bytes() == b'VALUE = 1\r\n'
