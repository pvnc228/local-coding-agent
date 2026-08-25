"""Plan Mode Controller, Structured Questions & Dynamic Checklist (Milestone R27).

Adapted from DeepSeek Harness @deepseek-ai/dsh-plan-mode, @deepseek-ai/dsh-tool-ask-user,
and @deepseek-ai/dsh-tool-todo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
import uuid


class PlanModeState(str, Enum):
    INACTIVE = "inactive"
    EXPLORING = "exploring"
    PLAN_READY = "plan_ready"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanModeError(RuntimeError):
    """Base error for plan mode lifecycle violations."""


class PlanModePolicyError(PlanModeError):
    """Raised when a blocked tool is invoked under plan mode restrictions."""


@dataclass
class PlanArtifact:
    goal: str
    steps: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    files_to_modify: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": list(self.steps),
            "risks": list(self.risks),
            "files_to_modify": list(self.files_to_modify),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanArtifact:
        return cls(
            goal=str(data.get("goal", "")),
            steps=[str(s) for s in data.get("steps", [])],
            risks=[str(r) for r in data.get("risks", [])],
            files_to_modify=[str(f) for f in data.get("files_to_modify", [])],
        )

    def render_markdown(self) -> str:
        lines = [f"# Plan: {self.goal}", ""]
        lines.append("## Steps")
        if self.steps:
            for idx, step in enumerate(self.steps, 1):
                lines.append(f"{idx}. {step}")
        else:
            lines.append("_No steps specified._")
        lines.append("")
        lines.append("## Risks")
        if self.risks:
            for risk in self.risks:
                lines.append(f"- {risk}")
        else:
            lines.append("_No identified risks._")
        lines.append("")
        lines.append("## Files to Modify")
        if self.files_to_modify:
            for f in self.files_to_modify:
                lines.append(f"- `{f}`")
        else:
            lines.append("_None specified._")
        return "\n".join(lines)


# Read-only tools allowed during plan exploration
READ_ONLY_TOOLS = frozenset({
    "read_file",
    "grep",
    "search_text",
    "ripgrep",
    "lsp",
    "list_files",
    "ast_skeleton",
    "doctor",
    "ask_user_question",
    "todo_write",
    "todo_get",
    "submit_plan",
    "exit_plan_mode",
})

# Write / mutation tools strictly blocked during exploration until plan is approved
WRITE_MUTATION_TOOLS = frozenset({
    "propose_patch",
    "apply",
    "apply_patch",
    "write_file",
    "edit_file",
    "delete_file",
    "create_file",
    "replace_file_content",
})


class PlanModeController:
    """Controls the lifecycle of plan mode, enforces tool policy, and manages approvals."""

    def __init__(self) -> None:
        self.state: PlanModeState = PlanModeState.INACTIVE
        self.goal: str | None = None
        self.current_plan: PlanArtifact | None = None
        self.feedback: str | None = None
        self.history: list[dict[str, Any]] = []

    @property
    def is_active(self) -> bool:
        return self.state != PlanModeState.INACTIVE

    def enter_plan_mode(self, goal: str) -> None:
        if not goal or not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        self.goal = goal.strip()
        self.state = PlanModeState.EXPLORING
        self.current_plan = None
        self.feedback = None
        self._record_event("enter_plan_mode", {"goal": self.goal})

    def exit_plan_mode(self) -> None:
        self.state = PlanModeState.INACTIVE
        self._record_event("exit_plan_mode", {})

    def submit_plan(self, plan: PlanArtifact | dict[str, Any]) -> None:
        if self.state not in {PlanModeState.EXPLORING, PlanModeState.PLAN_READY, PlanModeState.REJECTED}:
            raise PlanModeError(
                f"Cannot submit plan in state '{self.state.value}'. Must be in exploring or rejected state."
            )
        if isinstance(plan, dict):
            plan_obj = PlanArtifact.from_dict(plan)
        elif isinstance(plan, PlanArtifact):
            plan_obj = plan
        else:
            raise ValueError("plan must be PlanArtifact or dict")
        if not plan_obj.goal or not plan_obj.goal.strip():
            raise ValueError("plan must have a non-empty goal")
        self.current_plan = plan_obj
        self.state = PlanModeState.PLAN_READY
        self._record_event("submit_plan", plan_obj.to_dict())

    def approve_plan(self) -> None:
        if self.state != PlanModeState.PLAN_READY:
            raise PlanModeError(
                f"Cannot approve plan in state '{self.state.value}'. Plan must be in 'plan_ready' state."
            )
        self.state = PlanModeState.APPROVED
        self._record_event("approve_plan", {})

    def reject_plan(self, feedback: str) -> None:
        if self.state != PlanModeState.PLAN_READY:
            raise PlanModeError(
                f"Cannot reject plan in state '{self.state.value}'. Plan must be in 'plan_ready' state."
            )
        if not feedback or not isinstance(feedback, str) or not feedback.strip():
            raise ValueError("feedback must be a non-empty string")
        self.feedback = feedback.strip()
        self.state = PlanModeState.REJECTED
        self._record_event("reject_plan", {"feedback": self.feedback})

    def _is_safe_read_tool(self, tool: str) -> bool:
        if tool in READ_ONLY_TOOLS:
            return True
        if tool in WRITE_MUTATION_TOOLS or tool in {
            "propose_patch",
            "apply",
            "bash",
            "terminal_execute",
            "terminal_create",
            "run_command",
            "exec_command",
        }:
            return False
        if (
            tool.startswith("read_")
            or tool.startswith("list_")
            or tool.startswith("search_")
            or tool.endswith("_search")
            or tool.startswith("get_")
            or tool.endswith("_get")
        ):
            return True
        return False

    def is_tool_allowed(self, tool_name: str) -> bool:
        if not tool_name or not isinstance(tool_name, str):
            return False
        tool = tool_name.strip().lower()
        if self.state in {PlanModeState.INACTIVE, PlanModeState.APPROVED}:
            return True
        if self.state in {PlanModeState.EXPLORING, PlanModeState.PLAN_READY, PlanModeState.REJECTED}:
            return self._is_safe_read_tool(tool)
        return False

    def check_tool_allowed(self, tool_name: str) -> None:
        if not self.is_tool_allowed(tool_name):
            raise PlanModePolicyError(
                f"Tool '{tool_name}' is blocked in {self.state.value} mode. "
                f"Mutation/write tools are not permitted until the plan is approved."
            )

    def get_prompt_guidance(self) -> str:
        if self.state == PlanModeState.EXPLORING:
            return (
                "PLAN MODE ACTIVE: You are in read-only planning exploration mode. "
                "Investigate the codebase using read_file, search_text, grep, lsp, and list_files. "
                "Do NOT propose patches or modify files until you produce and submit a plan using submit_plan "
                "and the user approves it."
            )
        if self.state == PlanModeState.REJECTED:
            return (
                f"PLAN REJECTED: User feedback: {self.feedback}. "
                "Please revise your investigation and submit an updated plan."
            )
        if self.state == PlanModeState.PLAN_READY:
            return "PLAN SUBMITTED: Awaiting user approval."
        if self.state == PlanModeState.APPROVED:
            return "PLAN APPROVED: You may now execute the plan and propose changes."
        return ""

    def _record_event(self, action: str, data: dict[str, Any]) -> None:
        self.history.append({"action": action, "state": self.state.value, "data": data})


@dataclass
class QuestionItem:
    question: str
    options: list[str] = field(default_factory=list)
    is_multi_select: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    header: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str) or not self.id.strip():
            self.id = str(uuid.uuid4())[:8]
        else:
            self.id = self.id.strip()
        # Clean options: remove empty/whitespace-only options
        cleaned_opts: list[str] = []
        for opt in self.options:
            s_opt = str(opt).strip()
            if s_opt and s_opt not in cleaned_opts:
                cleaned_opts.append(s_opt)
        self.options = cleaned_opts

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "is_multi_select": self.is_multi_select,
        }
        if self.header:
            data["header"] = self.header
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionItem:
        raw_id = data.get("id")
        q_id = str(raw_id).strip() if raw_id and str(raw_id).strip() else str(uuid.uuid4())[:8]
        question = str(data.get("question", ""))
        options = [str(o) for o in data.get("options", [])]
        raw_multi = data.get("is_multi_select", data.get("multi_select", False))
        if isinstance(raw_multi, str):
            is_multi = raw_multi.strip().lower() in {"true", "1", "yes"}
        elif isinstance(raw_multi, (int, float)):
            is_multi = bool(raw_multi)
        else:
            is_multi = bool(raw_multi)
        header = data.get("header")
        return cls(
            id=q_id,
            question=question,
            options=options,
            is_multi_select=is_multi,
            header=str(header) if header else None,
        )


@dataclass
class QuestionAnswer:
    id: str
    selected: list[str] = field(default_factory=list)
    custom: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {"id": self.id, "selected": list(self.selected)}
        if self.custom is not None:
            res["custom"] = self.custom
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionAnswer:
        q_id = str(data.get("id", ""))
        raw_sel = data.get("selected", [])
        if isinstance(raw_sel, str):
            selected = [raw_sel]
        elif isinstance(raw_sel, list):
            selected = [str(s) for s in raw_sel]
        else:
            selected = []
        custom = data.get("custom")
        return cls(id=q_id, selected=selected, custom=str(custom) if custom is not None else None)


class AskUserQuestionTool:
    """Tool allowing the model to ask structured questions with customizable response simulation."""

    def __init__(
        self,
        answer_provider: Callable[[QuestionItem], QuestionAnswer | dict[str, Any] | list[str] | str] | None = None,
        preset_answers: dict[str, Any] | None = None,
    ) -> None:
        self.answer_provider = answer_provider
        self.preset_answers: dict[str, Any] = dict(preset_answers or {})
        self.history: list[dict[str, Any]] = []

    def set_preset_answer(self, question_id_or_text: str, answer: Any) -> None:
        self.preset_answers[question_id_or_text] = answer

    def set_answer_provider(
        self,
        provider: Callable[[QuestionItem], QuestionAnswer | dict[str, Any] | list[str] | str] | None,
    ) -> None:
        self.answer_provider = provider

    def ask(self, questions: list[QuestionItem | dict[str, Any]]) -> list[dict[str, Any]]:
        return self.ask_questions(questions)

    def ask_questions(self, questions: list[QuestionItem | dict[str, Any]]) -> list[dict[str, Any]]:
        if not questions:
            raise ValueError("questions list cannot be empty")
        parsed_questions: list[QuestionItem] = []
        for q in questions:
            if isinstance(q, QuestionItem):
                parsed_questions.append(q)
            elif isinstance(q, dict):
                parsed_questions.append(QuestionItem.from_dict(q))
            else:
                raise ValueError("Each question must be a QuestionItem or dict")

        seen_q_ids: set[str] = set()
        for q in parsed_questions:
            if not q.question or not q.question.strip():
                raise ValueError("Question text must be non-empty")
            if q.id in seen_q_ids:
                raise ValueError(f"Duplicate question id '{q.id}' in questions list")
            seen_q_ids.add(q.id)

        results: list[dict[str, Any]] = []
        for q in parsed_questions:
            ans = self._resolve_answer(q)
            results.append(ans.to_dict())

        self.history.append({
            "questions": [q.to_dict() for q in parsed_questions],
            "answers": results,
        })
        return results

    def _resolve_answer(self, q: QuestionItem) -> QuestionAnswer:
        if q.id in self.preset_answers:
            raw = self.preset_answers[q.id]
            return self._normalize_answer(q, raw)
        if q.question in self.preset_answers:
            raw = self.preset_answers[q.question]
            return self._normalize_answer(q, raw)

        if self.answer_provider is not None:
            raw = self.answer_provider(q)
            return self._normalize_answer(q, raw)

        if q.options:
            return QuestionAnswer(id=q.id, selected=[q.options[0]])
        return QuestionAnswer(id=q.id, selected=[], custom=None)

    def _normalize_answer(self, q: QuestionItem, raw: Any) -> QuestionAnswer:
        if isinstance(raw, QuestionAnswer):
            ans = raw
        elif isinstance(raw, dict):
            ans = QuestionAnswer.from_dict({"id": q.id, **raw})
        elif isinstance(raw, list):
            cleaned_list: list[str] = []
            for s in raw:
                str_s = str(s).strip()
                if str_s and str_s not in cleaned_list:
                    cleaned_list.append(str_s)
            ans = QuestionAnswer(id=q.id, selected=cleaned_list)
        elif isinstance(raw, str):
            clean_str = raw.strip()
            if q.options and clean_str in q.options:
                ans = QuestionAnswer(id=q.id, selected=[clean_str])
            else:
                ans = QuestionAnswer(id=q.id, selected=[], custom=raw)
        else:
            ans = QuestionAnswer(id=q.id, selected=[])

        if not q.is_multi_select and len(ans.selected) > 1:
            raise ValueError(f"Question '{q.id}' does not allow multi-select, but got {len(ans.selected)} choices")

        return ans

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_questions = arguments.get("questions", [])
        if not isinstance(raw_questions, list):
            raise ValueError("arguments['questions'] must be a list")
        answers = self.ask_questions(raw_questions)
        return {"answers": answers}

    def get_tool_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "ask_user_question",
                "description": "Ask the user one or more clarifying questions before proceeding.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "question": {"type": "string"},
                                    "header": {"type": "string"},
                                    "options": {"type": "array", "items": {"type": "string"}},
                                    "is_multi_select": {"type": "boolean"},
                                },
                                "required": ["question"],
                            },
                        }
                    },
                    "required": ["questions"],
                },
            },
        }


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TodoItem:
    id: str
    content: str
    status: TodoStatus | str = TodoStatus.PENDING

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Todo id must be a non-empty string")
        self.id = self.id.strip()
        if not self.content or not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Todo content must be a non-empty string")
        self.content = self.content.strip()
        if isinstance(self.status, str):
            status_val = self.status.strip().lower()
            if status_val not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"Invalid todo status '{self.status}'. Must be pending, in_progress, or completed.")
            self.status = TodoStatus(status_val)
        elif isinstance(self.status, TodoStatus):
            pass
        else:
            raise ValueError(f"Invalid todo status '{self.status}'. Must be pending, in_progress, or completed.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value if isinstance(self.status, TodoStatus) else str(self.status),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_id: str | None = None) -> TodoItem:
        raw_id = data.get("id")
        item_id = str(raw_id).strip() if raw_id and str(raw_id).strip() else (fallback_id or str(uuid.uuid4())[:8])
        content = str(data.get("content", ""))
        status = data.get("status", "pending")
        return cls(id=item_id, content=content, status=status)


class TodoChecklist:
    """Dynamic Todo checklist tool with full-list replacement, ASCII and markdown rendering."""

    def __init__(self, allow_parallel_in_progress: bool = True) -> None:
        self.allow_parallel_in_progress = allow_parallel_in_progress
        self._items: list[TodoItem] = []

    @property
    def items(self) -> list[TodoItem]:
        return list(self._items)

    def todo_write(self, items: list[dict[str, Any] | TodoItem]) -> list[dict[str, Any]]:
        parsed_items: list[TodoItem] = []
        seen_ids: set[str] = set()
        seen_contents: set[str] = set()
        active_count = 0

        for idx, item in enumerate(items, 1):
            if isinstance(item, TodoItem):
                todo = item
            elif isinstance(item, dict):
                todo = TodoItem.from_dict(item, fallback_id=f"todo_{idx}")
            else:
                raise ValueError("Item must be TodoItem or dict")

            if todo.id in seen_ids:
                raise ValueError(f"Duplicate todo id: '{todo.id}'")
            seen_ids.add(todo.id)

            cleaned = todo.content.strip()
            if cleaned in seen_contents:
                raise ValueError(f"Duplicate todo content: '{cleaned}'")
            seen_contents.add(cleaned)

            if todo.status == TodoStatus.IN_PROGRESS:
                active_count += 1

            parsed_items.append(todo)

        if not self.allow_parallel_in_progress and active_count > 1:
            raise ValueError(f"At most one todo may be in_progress when parallel is disabled (got {active_count})")

        self._items = parsed_items
        return self.todo_get()

    def todo_get(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items]

    def todo_update(self, item_id: str, *, status: str | None = None, content: str | None = None) -> dict[str, Any]:
        for idx, item in enumerate(self._items):
            if item.id == item_id:
                new_content = content if content is not None else item.content
                new_status = status if status is not None else item.status
                updated = TodoItem(id=item.id, content=new_content, status=new_status)
                if not self.allow_parallel_in_progress and updated.status == TodoStatus.IN_PROGRESS:
                    other_active = any(
                        other.id != item_id and other.status == TodoStatus.IN_PROGRESS
                        for other in self._items
                    )
                    if other_active:
                        raise ValueError("At most one todo may be in_progress when parallel is disabled")
                self._items[idx] = updated
                return updated.to_dict()
        raise KeyError(f"Todo item with id '{item_id}' not found")

    def counts(self) -> dict[str, int]:
        p = sum(1 for item in self._items if item.status == TodoStatus.PENDING)
        ip = sum(1 for item in self._items if item.status == TodoStatus.IN_PROGRESS)
        c = sum(1 for item in self._items if item.status == TodoStatus.COMPLETED)
        return {
            "pending": p,
            "in_progress": ip,
            "completed": c,
            "total": len(self._items),
        }

    def render_ascii(self) -> str:
        if not self._items:
            return "No todos in checklist."
        lines: list[str] = []
        for item in self._items:
            if item.status == TodoStatus.COMPLETED:
                symbol = "[x]"
            elif item.status == TodoStatus.IN_PROGRESS:
                symbol = "[>]"
            else:
                symbol = "[ ]"
            lines.append(f"{symbol} #{item.id} {item.content}")
        c = self.counts()
        lines.append("---")
        lines.append(f"{c['completed']}/{c['total']} completed ({c['in_progress']} in progress, {c['pending']} pending)")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        if not self._items:
            return "_No todos in checklist._"
        lines: list[str] = []
        for item in self._items:
            if item.status == TodoStatus.COMPLETED:
                lines.append(f"- [x] {item.content}")
            elif item.status == TodoStatus.IN_PROGRESS:
                lines.append(f"- [ ] **(in progress)** {item.content}")
            else:
                lines.append(f"- [ ] {item.content}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "todo_write":
            raw_items = arguments.get("todos", arguments.get("items", []))
            if not isinstance(raw_items, list):
                raise ValueError("todos must be a list")
            todos = self.todo_write(raw_items)
            return {"todos": todos, "counts": self.counts()}
        elif name == "todo_get":
            return {"todos": self.todo_get(), "counts": self.counts()}
        else:
            raise ValueError(f"Unknown todo tool: {name}")

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "todo_write",
                    "description": "Record and update the structured task list for current work (replaces full list).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "todos": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "content": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                        },
                                    },
                                    "required": ["content", "status"],
                                },
                            }
                        },
                        "required": ["todos"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "todo_get",
                    "description": "Get current task list and status counts.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
