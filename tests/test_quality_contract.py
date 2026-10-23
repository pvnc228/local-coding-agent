import json
import os
import subprocess
import sys

import pytest

from local_coding_agent.cli import build_parser
from local_coding_agent.service import DelegationRequest, DelegationService
from local_coding_agent.task import TaskEnvelope


def test_cli_exposes_quality_options():
    args = build_parser().parse_args(['delegate', '--task', '{}', '--repair-rounds', '0',
                                     '--samples', '3', '--strategy', 'tests', '--max-model-calls', '12'])
    assert (args.repair_rounds, args.samples, args.strategy, args.max_model_calls) == (0, 3, 'tests', 12)
    assert build_parser().parse_args(['delegate', '--task', '{}']).repair_rounds == 2
    bench = build_parser().parse_args(['benchmark', '--suite', 'hard', '--endpoint', 'http://127.0.0.1:8082'])
    assert bench.suite == 'hard' and bench.endpoint.endswith(':8082')


def test_request_options_validate_and_are_fingerprinted(tmp_path):
    payload = {'request_id': 'x', 'workspace_ref': 'repo', 'model_profile': 'qwen2.5-coder',
               'task': {'id': 'x', 'goal': 'x', 'files': ['a.py']},
               'repair_rounds': 0, 'samples': 2, 'strategy': 'single', 'max_model_calls': 4}
    request = DelegationRequest.from_mapping(payload)
    assert request.repair_rounds == 0 and request.samples == 2
    other = DelegationRequest.from_mapping({**payload, 'repair_rounds': 2})
    assert DelegationService._fingerprint(request) != DelegationService._fingerprint(other)
    for field, value in [('repair_rounds', 6), ('samples', 0), ('samples', True), ('strategy', 'oops'), ('max_model_calls', 0)]:
        with pytest.raises(ValueError):
            DelegationRequest.from_mapping({**payload, field: value})


def test_service_and_cli_use_the_same_quality_policy(tmp_path, monkeypatch, capsys):
    from local_coding_agent.cli import main
    (tmp_path / 'a.py').write_text('VALUE = 1\n')
    class Model:
        def chat(self, messages, **kwargs):
            return {'message': {'content': json.dumps({'summary': 'fix', 'checks': [], 'risks': [],
                'edits': [{'file': 'a.py', 'search': 'VALUE = 1\n', 'replace': 'VALUE = 2\n'}]})}}
    task = TaskEnvelope(id='x', goal='x', files=('a.py',), checks=('python -c "from a import VALUE; assert VALUE == 2"',))
    service = DelegationService({'repo': tmp_path}, model_factory=lambda profile: Model())
    request = DelegationRequest('x', 'repo', 'qwen2.5-coder', task)
    result = service.delegate('test', request)
    assert result['status'] == 'accepted'
    assert result['verification']['mode'] == 'scratch'
    monkeypatch.setattr('local_coding_agent.cli._handlers.build_client', lambda profile: Model())
    code = main(['delegate', '--task', json.dumps(task.as_dict()), '--workspace', str(tmp_path), '--json'])
    cli = json.loads(capsys.readouterr().out)
    assert code == 0
    for field in ('status', 'verification', 'selection', 'model_calls'):
        assert cli[field] == result[field]


def test_delegate_json_survives_cp1252_console(tmp_path):
    env = {**os.environ, 'PYTHONIOENCODING': 'cp1252'}
    result = subprocess.run([sys.executable, '-m', 'local_coding_agent', 'delegate',
                             '--task-file', str(tmp_path / 'ошибка-😀.json'), '--json'],
                            env=env, capture_output=True)
    assert result.returncode == 1
    assert json.loads(result.stdout.decode('ascii'))['error']['kind'] == 'input'


def test_thinking_opt_in_reaches_same_cli_and_service_profile(tmp_path, monkeypatch, capsys):
    from local_coding_agent.cli import main
    profiles = []
    class Model:
        def chat(self, messages, **kwargs):
            return {'message': {'content': json.dumps({'summary': 'read', 'patch': '', 'checks': [], 'risks': []})}}
    def build(profile):
        profiles.append(profile)
        return Model()
    task = TaskEnvelope(id='think', goal='read', files=('a.py',))
    service = DelegationService({'repo': tmp_path}, model_factory=build)
    result = service.delegate('test', DelegationRequest('think', 'repo', 'qwen2.5-coder', task, think=True))
    assert result['status'] == 'accepted' and profiles[-1].think is True
    monkeypatch.setattr('local_coding_agent.cli._handlers.build_client', build)
    assert main(['delegate', '--task', json.dumps(task.as_dict()), '--workspace', str(tmp_path),
                 '--think', '--num-predict', '1536', '--json']) == 0
    capsys.readouterr()
    assert profiles[-1].think is True and profiles[-1].output_num_predict == 3072
