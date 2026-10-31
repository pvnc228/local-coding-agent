import json
from unittest.mock import patch

from local_coding_agent.controller import Controller
from local_coding_agent.quality import run_quality
from local_coding_agent.task import TaskEnvelope


def setup(root, checks=()):
    (root / 'a.py').write_text('VALUE = 1\n')
    return TaskEnvelope(id='samples', goal='fix', files=('a.py',), checks=checks)


def factory(root, values, requests):
    def make(index):
        class Model:
            def chat(self, messages, **kwargs):
                requests.append(index)
                return {'message': {'role': 'assistant', 'content': json.dumps({
                    'summary': 'fix', 'patch': '', 'checks': [], 'risks': [],
                    'edits': [{'file': 'a.py', 'search': 'VALUE = 1\n', 'replace': f'VALUE = {values[index]}\n'}]})}}
        return Controller(Model(), root, max_turns=1, max_repair_rounds=0)
    return make


def test_samples_stop_at_first_tested_candidate(tmp_path):
    task = setup(tmp_path, ('python -c "from a import VALUE; assert VALUE == 3"',))
    requests = []
    result = run_quality(task, factory(tmp_path, [2, 3, 4], requests), samples=3)
    assert result['status'] == 'accepted'
    assert result['selection'] == 'tests'
    assert result['model_calls'] == 2
    assert requests == [0, 1]
    assert (tmp_path / 'a.py').read_text() == 'VALUE = 1\n'


def test_samples_without_checks_choose_majority(tmp_path):
    task = setup(tmp_path)
    result = run_quality(task, factory(tmp_path, [2, 3, 3], []), samples=3)
    assert result['selection'] == 'majority'
    assert 'VALUE = 3' in result['patch']
    assert result['verification']['mode'] == 'none'


def test_duplicate_patch_is_not_tested_twice(tmp_path):
    task = setup(tmp_path, ('python -c "raise SystemExit(1)"',))
    with patch('local_coding_agent.quality.verify_in_scratch') as verify:
        from local_coding_agent.controller._scratch import ScratchVerdict
        verify.return_value = ScratchVerdict(False, [{'passed': False}], 'failed')
        result = run_quality(task, factory(tmp_path, [2, 2, 3], []), samples=3)
    assert verify.call_count == 2
    assert result['status'] == 'rejected'
    assert result['duplicates'] == 1


def test_global_model_call_cap_stops_samples(tmp_path):
    task = setup(tmp_path)
    requests = []
    result = run_quality(task, factory(tmp_path, [2, 3, 4], requests), samples=3, max_model_calls=2)
    assert requests == [0, 1]
    assert result['model_calls'] == 2
    assert result['budget_exhausted']


def test_single_default_does_not_force_checks_with_explicit_zero(tmp_path):
    task = setup(tmp_path, ('python -c "raise SystemExit(1)"',))
    result = run_quality(task, factory(tmp_path, [2], []))
    assert result['status'] == 'accepted'
    assert result['verification']['mode'] == 'none'


def test_budget_exhaustion_inside_repair_has_distinct_error(tmp_path):
    task = setup(tmp_path, ('python -c "raise SystemExit(1)"',))
    build = factory(tmp_path, [2], [])
    def repairing(index):
        controller = build(index)
        controller.max_repair_rounds = 2
        return controller
    result = run_quality(task, repairing, max_model_calls=1)
    assert result['error']['kind'] == 'model_call_budget_exhausted'
    assert result['model_calls'] == 1
    assert result['budget_exhausted'] is True


def test_samples_vary_instruction_without_changing_model_scope(tmp_path):
    task = setup(tmp_path)
    build = factory(tmp_path, [2, 3, 3], [])
    controllers = []
    def record(index):
        controller = build(index)
        controllers.append(controller)
        return controller
    run_quality(task, record, samples=3)
    assert len({c.system_contract for c in controllers}) == 3
