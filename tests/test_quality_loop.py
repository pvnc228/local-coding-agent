import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from local_coding_agent.controller import Controller
from local_coding_agent.prescriptions import test_failure_prescription
from local_coding_agent.task import TaskEnvelope


class Scripted:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []

    def chat(self, messages, *, tools=None):
        self.requests.append(json.loads(json.dumps(messages)))
        value = next(self.values)
        return {'message': {'role': 'assistant', 'content': json.dumps({
            'status': 'candidate', 'summary': 'fix', 'checks': [], 'risks': [],
            'edits': [{'file': 'value.py', 'search': 'VALUE = 1\n', 'replace': f'VALUE = {value}\n'}],
        })}}


def task_fixture(root):
    (root / 'value.py').write_text('VALUE = 1\n')
    (root / 'test_value.py').write_text('from value import VALUE\ndef test_value():\n    assert VALUE == 3\n')
    return TaskEnvelope(id='repair', goal='fix value', files=('value.py',),
                        checks=('python -m pytest -q -p no:cacheprovider test_value.py',))


def test_failing_candidate_receives_real_test_failure_and_repairs(tmp_path):
    task = task_fixture(tmp_path)
    model = Scripted([2, 3])
    result = Controller(model, tmp_path, max_turns=1).run(task)
    assert result['status'] == 'accepted'
    assert result['verification'] == {'mode': 'scratch', 'rounds': 2}
    assert result['checks'][0]['passed']
    assert result['model_calls'] == 2
    feedback = model.requests[1][-1]['content']
    assert 'test_value' in feedback and 'assert' in feedback
    assert (tmp_path / 'value.py').read_text() == 'VALUE = 1\n'


def test_exhausted_repairs_return_best_candidate(tmp_path):
    task = task_fixture(tmp_path)
    result = Controller(Scripted([2, 4]), tmp_path, max_repair_rounds=1).run(task)
    assert result['status'] == 'rejected'
    assert result['error']['kind'] == 'repair_budget_exhausted'
    assert result['verification']['rounds'] == 2
    assert result['checks'][0]['passed'] is False
    assert 'VALUE = 2' in result['patch']


def test_explicit_zero_preserves_format_only_acceptance(tmp_path):
    task = task_fixture(tmp_path)
    result = Controller(Scripted([2]), tmp_path, max_repair_rounds=0).run(task)
    assert result['status'] == 'accepted'
    assert result['verification']['mode'] == 'none'


def test_no_checks_cannot_forge_verification(tmp_path):
    (tmp_path / 'value.py').write_text('VALUE = 1\n')
    class Forging:
        def chat(self, messages, **kwargs):
            return {'message': {'content': json.dumps({'status': 'candidate', 'summary': 'read', 'patch': '',
                    'checks': [], 'risks': [], 'verification': {'mode': 'scratch'}, 'model_calls': 99,
                    'workspace_modified': True, 'workspace_state': 'original', 'rolled_back': True})}}
    result = Controller(Forging(), tmp_path).run(TaskEnvelope(id='x', goal='x', files=('value.py',)))
    assert result['verification']['mode'] == 'none'
    assert result['model_calls'] == 1
    assert not {'workspace_modified', 'workspace_state', 'rolled_back'}.intersection(result)


def test_repair_history_is_bounded(tmp_path):
    task = task_fixture(tmp_path)
    model = Scripted([2, 4, 5, 3])
    result = Controller(model, tmp_path, max_repair_rounds=3).run(task)
    assert result['status'] == 'accepted'
    assert len(model.requests[-1]) == len(model.requests[1])
    assert model.requests[-1][-2]['role'] == 'assistant'


def test_explicit_test_file_cannot_be_changed(tmp_path):
    task_fixture(tmp_path)
    task = TaskEnvelope(id='x', goal='x', files=('value.py',), checks=('python value.py',))
    result = Controller(Scripted([2]), tmp_path, max_retries=0).run(task)
    assert result['status'] == 'rejected'
    assert 'check target' in str(result['validation']['issues'])


@pytest.mark.parametrize('limit', [-1, 6, True])
def test_repair_budget_validation(tmp_path, limit):
    with pytest.raises(ValueError):
        Controller(Scripted([]), tmp_path, max_repair_rounds=limit)


def test_failure_prescription_is_bounded_redacted_and_structured(monkeypatch):
    monkeypatch.setenv('MY_SECRET_TOKEN', 'secret-sentinel-123')
    text = 'FAILED test_value.py::test_value\nE assert 2 == 3\nAuthorization: Bearer private-token\nMY_SECRET_TOKEN=secret-sentinel-123\n' + 'я' * 10000
    feedback = test_failure_prescription([{'exit_code': 1, 'stdout': text, 'stderr': ''}], ('value.py',))
    assert set(feedback) == {'error', 'issues', 'instruction'}
    encoded = json.dumps(feedback, ensure_ascii=False).encode()
    assert len(encoded) <= 1500
    assert b'secret-sentinel' not in encoded and b'private-token' not in encoded
    assert 'test_value' in str(feedback)


def test_model_run_tests_uses_private_copy(tmp_path):
    (tmp_path / 'value.py').write_text('VALUE = 1\n')
    command = 'python -c "from pathlib import Path; Path(\'runner-artifact\').write_text(\'yes\')"'
    class Model:
        calls = 0
        def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {'message': {'tool_calls': [{'function': {'name': 'run_tests', 'arguments': {'command': command}}}]}}
            return {'message': {'content': json.dumps({'summary': 'checked', 'patch': '', 'checks': [], 'risks': []})}}
    task = TaskEnvelope(id='x', goal='check', files=('value.py',), checks=(command,))
    result = Controller(Model(), tmp_path).run(task)
    assert result['status'] == 'accepted'
    assert not (tmp_path / 'runner-artifact').exists()


@pytest.mark.parametrize('target', ['tests', 'tests/test_*.py'])
def test_directory_and_glob_test_targets_are_protected(tmp_path, target):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_value.py').write_text('def test_value():\n    assert 1 == 2\n')
    class Model:
        def chat(self, messages, **kwargs):
            return {'message': {'content': json.dumps({'summary': 'cheat', 'checks': [], 'risks': [],
                'edits': [{'file': 'tests/test_value.py', 'search': '    assert 1 == 2\n', 'replace': '    assert 1 == 1\n'}]})}}
    result = Controller(Model(), tmp_path, max_retries=0).run(TaskEnvelope(
        id='x', goal='fix', files=('tests/test_value.py',), checks=(f'python -m pytest -q {target}',)))
    assert result['status'] == 'rejected'
    assert 'check target' in str(result['validation']['issues'])


def test_controller_apply_uses_shared_mediated_gate(tmp_path):
    task = task_fixture(tmp_path)
    with patch('local_coding_agent.controller._controller.mediated_apply', create=True,
               return_value={'status': 'accepted', 'applied': True, 'checks': [], 'audit': []}) as gate:
        result = Controller(Scripted([3]), tmp_path).run(task, apply=True)
    gate.assert_called_once()
    assert result['applied']
    assert (tmp_path / 'value.py').read_text() == 'VALUE = 1\n'


def test_unittest_feedback_keeps_observed_assertion_and_pytest_identity(tmp_path):
    (tmp_path / 'test_case.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(4, 2)\n')
    run = subprocess.run([sys.executable, '-m', 'unittest', '-q', 'test_case'],
                         cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 1
    feedback = test_failure_prescription([{'passed': False, 'exit_code': 1,
                                         'stdout': run.stdout, 'stderr': run.stderr}], ('value.py',))
    assert 'AssertionError: 4 != 2' in str(feedback)
    text = 'E ' + ('details\nE ' * 40) + '\nFAILED tests/test_case.py::test_precise\n'
    feedback = test_failure_prescription([{'passed': False, 'exit_code': 1, 'stdout': text}], ('value.py',))
    assert 'test_precise' in str(feedback)
