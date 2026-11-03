"""Shared bounded quality execution for CLI and the transport-neutral service."""
from __future__ import annotations

from collections import Counter
from threading import Condition, Event, Thread
from typing import Any, Callable

from .controller import Controller
from .controller._scratch import verify_in_scratch
from .task import TaskEnvelope


class ModelCallBudgetExceeded(ValueError):
    kind = 'model_call_budget_exhausted'


class _CallBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.calls = 0
        self.active = 0
        self.tokens: int | None = 0
        self.condition = Condition()

    def wrap(self, model):
        budget = self

        class BudgetModel:
            def chat(self, messages, *, tools=None):
                with budget.condition:
                    if budget.calls >= budget.limit:
                        raise ModelCallBudgetExceeded('max_model_calls exhausted')
                    budget.calls += 1
                    budget.active += 1
                try:
                    response = model.chat(messages, tools=tools)
                    tokens = response.get('eval_count') if isinstance(response, dict) else None
                    with budget.condition:
                        budget.tokens = budget.tokens + tokens if budget.tokens is not None and type(tokens) is int else None
                    return response
                finally:
                    with budget.condition:
                        budget.active -= 1
                        budget.condition.notify_all()
        return BudgetModel()

    def complete_when_idle(self, event: Event | None):
        if event is None:
            return
        def wait():
            with self.condition:
                self.condition.wait_for(lambda: self.active == 0)
            event.set()
        with self.condition:
            if self.active == 0:
                event.set()
                return
        Thread(target=wait, daemon=True).start()


def run_quality(task: TaskEnvelope, controller_factory: Callable[[int], Controller], *,
                samples: int = 1, strategy: str = 'single', max_model_calls: int = 30,
                cancel_event: Event | None = None,
                completion_event: Event | None = None) -> dict[str, Any]:
    if type(samples) is not int or not 1 <= samples <= 5:
        raise ValueError('samples must be an integer between 1 and 5')
    if type(max_model_calls) is not int or not 1 <= max_model_calls <= 100:
        raise ValueError('max_model_calls must be an integer between 1 and 100')
    if strategy not in ('single', 'tests', 'plan', 'per_file', 'by_files'):
        raise ValueError('unknown delegation strategy')
    budget = _CallBudget(max_model_calls)
    accepted = []
    results = []
    checked = {}
    duplicates = 0
    scratch_cache = {}
    try:
        for index in range(samples):
            if cancel_event is not None and cancel_event.is_set():
                return {'status': 'failed', 'error': {'kind': 'cancelled', 'message': 'task was cancelled'},
                        'model_calls': budget.calls, 'eval_count': budget.tokens}
            if budget.calls >= budget.limit:
                break
            controller = controller_factory(index)
            if index:
                variants = (
                    'Prefer minimal SEARCH/REPLACE edits; reuse the tool-resolved patch in the final JSON.',
                    'Check boundary cases before answering. Keep the final proposal minimal and complete.',
                )
                controller.system_contract += '\n' + variants[(index - 1) % len(variants)]
            controller.model = budget.wrap(controller.model)
            controller.cancel_event = cancel_event or controller.cancel_event
            controller._scratch_cache = scratch_cache
            if strategy == 'single':
                result = controller.run(task)
            else:
                from .delegator import run_quality_strategy
                result = run_quality_strategy(controller, task, strategy)
            results.append(result)
            if result.get('status') != 'accepted':
                continue
            normalized = (result.get('patch') or '').replace('\r\n', '\n').strip()
            if samples > 1 and task.checks:
                if normalized in checked:
                    duplicates += 1
                    verdict = checked[normalized]
                elif result.get('verification', {}).get('mode') == 'scratch':
                    verdict = all(check.get('passed') is True for check in result.get('checks', [])) and bool(result.get('checks'))
                    checked[normalized] = verdict
                else:
                    observed = verify_in_scratch(controller.workspace_root, result.get('patch') or '', task,
                                                 cancel_event=controller.cancel_event, audit=result.setdefault('audit', []))
                    verdict = observed.passed
                    checked[normalized] = verdict
                    result['checks'] = observed.checks
                    result['verification'] = {'mode': 'scratch' if observed.state in ('passed', 'failed') else 'none', 'rounds': 1}
                if not verdict:
                    result['status'] = 'rejected'
                    continue
            accepted.append((normalized, result))
            if task.checks:
                break
        if accepted:
            counts = Counter(key for key, _ in accepted)
            winner = max(counts, key=counts.get)  # stable first-on-tie
            result = next(item for key, item in accepted if key == winner)
            result['selection'] = ('tests' if task.checks and result.get('verification', {}).get('mode') == 'scratch'
                                   else 'majority' if samples > 1 else 'format_only')
        else:
            result = results[0] if results else {'status': 'failed', 'error': {'kind': 'model_call_budget_exhausted'}}
        result['model_calls'] = budget.calls
        result['eval_count'] = budget.tokens
        result['samples_run'] = len(results)
        result['duplicates'] = duplicates
        result['budget_exhausted'] = budget.calls >= budget.limit
        return result
    finally:
        budget.complete_when_idle(completion_event)
