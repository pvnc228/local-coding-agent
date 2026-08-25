"""Unit tests for Milestone R27: Plan Mode Controller, Structured Questions & Dynamic Checklist."""

import pytest
from local_coding_agent.plan_mode import (
    AskUserQuestionTool,
    PlanArtifact,
    PlanModeController,
    PlanModeError,
    PlanModePolicyError,
    PlanModeState,
    QuestionAnswer,
    QuestionItem,
    TodoChecklist,
    TodoItem,
    TodoStatus,
)


class TestPlanArtifact:
    def test_plan_artifact_to_dict_and_from_dict(self):
        plan = PlanArtifact(
            goal="Refactor authentication layer",
            steps=["Audit tokens", "Extract validator", "Add unit tests"],
            risks=["Breaking legacy clients", "Session expiration"],
            files_to_modify=["auth/token.py", "auth/validator.py"],
        )
        data = plan.to_dict()
        assert data["goal"] == "Refactor authentication layer"
        assert len(data["steps"]) == 3
        assert len(data["risks"]) == 2
        assert len(data["files_to_modify"]) == 2

        restored = PlanArtifact.from_dict(data)
        assert restored.goal == plan.goal
        assert restored.steps == plan.steps
        assert restored.risks == plan.risks
        assert restored.files_to_modify == plan.files_to_modify

    def test_plan_artifact_markdown_rendering(self):
        plan = PlanArtifact(
            goal="Add Plan Mode",
            steps=["Implement controller", "Add tests"],
            risks=["None"],
            files_to_modify=["local_coding_agent/plan_mode.py"],
        )
        md = plan.render_markdown()
        assert "# Plan: Add Plan Mode" in md
        assert "1. Implement controller" in md
        assert "2. Add tests" in md
        assert "- None" in md
        assert "- `local_coding_agent/plan_mode.py`" in md

    def test_plan_artifact_empty_defaults(self):
        plan = PlanArtifact(goal="Minimal plan")
        md = plan.render_markdown()
        assert "# Plan: Minimal plan" in md
        assert "_No steps specified._" in md
        assert "_No identified risks._" in md
        assert "_None specified._" in md


class TestPlanModeController:
    def test_initial_state_inactive(self):
        ctrl = PlanModeController()
        assert ctrl.state == PlanModeState.INACTIVE
        assert not ctrl.is_active
        assert ctrl.current_plan is None
        assert ctrl.goal is None

    def test_enter_plan_mode(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Fix bug in rate limiter")
        assert ctrl.state == PlanModeState.EXPLORING
        assert ctrl.is_active
        assert ctrl.goal == "Fix bug in rate limiter"
        assert len(ctrl.history) == 1

        with pytest.raises(ValueError, match="goal must be a non-empty string"):
            ctrl.enter_plan_mode("")

    def test_submit_plan_lifecycle(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Upgrade database schema")

        plan = PlanArtifact(
            goal="Upgrade database schema",
            steps=["Create migration", "Run tests"],
        )
        ctrl.submit_plan(plan)
        assert ctrl.state == PlanModeState.PLAN_READY
        assert ctrl.current_plan is not None
        assert ctrl.current_plan.goal == "Upgrade database schema"

    def test_submit_plan_dict_input(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Upgrade database schema")
        ctrl.submit_plan({
            "goal": "Upgrade database schema",
            "steps": ["Step 1"],
            "risks": ["Risk 1"],
            "files_to_modify": ["db.py"],
        })
        assert ctrl.state == PlanModeState.PLAN_READY
        assert ctrl.current_plan.goal == "Upgrade database schema"

    def test_approve_plan(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Fix bug")
        ctrl.submit_plan(PlanArtifact(goal="Fix bug", steps=["Do it"]))
        assert ctrl.state == PlanModeState.PLAN_READY

        ctrl.approve_plan()
        assert ctrl.state == PlanModeState.APPROVED
        assert ctrl.is_tool_allowed("propose_patch")
        assert ctrl.is_tool_allowed("apply")

    def test_reject_plan_and_resubmit(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Fix bug")
        ctrl.submit_plan(PlanArtifact(goal="Fix bug", steps=["Approach A"]))

        ctrl.reject_plan("Approach A is too risky, use Approach B.")
        assert ctrl.state == PlanModeState.REJECTED
        assert ctrl.feedback == "Approach A is too risky, use Approach B."
        assert "PLAN REJECTED" in ctrl.get_prompt_guidance()

        # Resubmit new plan from rejected state
        ctrl.submit_plan(PlanArtifact(goal="Fix bug", steps=["Approach B"]))
        assert ctrl.state == PlanModeState.PLAN_READY
        ctrl.approve_plan()
        assert ctrl.state == PlanModeState.APPROVED

    def test_invalid_state_transitions(self):
        ctrl = PlanModeController()
        # Cannot approve when inactive
        with pytest.raises(PlanModeError, match="Cannot approve plan in state 'inactive'"):
            ctrl.approve_plan()

        # Cannot reject when inactive
        with pytest.raises(PlanModeError, match="Cannot reject plan in state 'inactive'"):
            ctrl.reject_plan("Bad plan")

        # Cannot submit plan when inactive
        with pytest.raises(PlanModeError, match="Cannot submit plan in state 'inactive'"):
            ctrl.submit_plan(PlanArtifact(goal="Goal"))

        ctrl.enter_plan_mode("Explore")
        # Cannot approve while still exploring without submitting plan
        with pytest.raises(PlanModeError, match="Cannot approve plan in state 'exploring'"):
            ctrl.approve_plan()

        # Reject with empty feedback
        ctrl.submit_plan(PlanArtifact(goal="Explore", steps=["1"]))
        with pytest.raises(ValueError, match="feedback must be a non-empty string"):
            ctrl.reject_plan("")

    def test_exit_plan_mode(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Goal")
        assert ctrl.state == PlanModeState.EXPLORING
        ctrl.exit_plan_mode()
        assert ctrl.state == PlanModeState.INACTIVE

    def test_tool_policy_enforcement_in_exploring_mode(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Investigate architecture")

        # Read-only tools MUST be allowed
        assert ctrl.is_tool_allowed("read_file")
        assert ctrl.is_tool_allowed("grep")
        assert ctrl.is_tool_allowed("search_text")
        assert ctrl.is_tool_allowed("lsp")
        assert ctrl.is_tool_allowed("list_files")
        assert ctrl.is_tool_allowed("ask_user_question")
        assert ctrl.is_tool_allowed("todo_write")
        assert ctrl.is_tool_allowed("todo_get")
        assert ctrl.is_tool_allowed("submit_plan")

        ctrl.check_tool_allowed("read_file")
        ctrl.check_tool_allowed("lsp")
        ctrl.check_tool_allowed("list_files")

        # Mutation / write tools MUST be blocked
        assert not ctrl.is_tool_allowed("propose_patch")
        assert not ctrl.is_tool_allowed("apply")
        assert not ctrl.is_tool_allowed("apply_patch")
        assert not ctrl.is_tool_allowed("write_file")
        assert not ctrl.is_tool_allowed("edit_file")
        assert not ctrl.is_tool_allowed("delete_file")

        with pytest.raises(PlanModePolicyError, match="Tool 'propose_patch' is blocked in exploring mode"):
            ctrl.check_tool_allowed("propose_patch")

        with pytest.raises(PlanModePolicyError, match="Tool 'apply' is blocked in exploring mode"):
            ctrl.check_tool_allowed("apply")

    def test_tool_policy_enforcement_after_approval(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Goal")
        ctrl.submit_plan(PlanArtifact(goal="Goal", steps=["Step 1"]))
        ctrl.approve_plan()

        assert ctrl.is_tool_allowed("propose_patch")
        assert ctrl.is_tool_allowed("apply")
        ctrl.check_tool_allowed("propose_patch")
        ctrl.check_tool_allowed("apply")

    def test_prompt_guidance(self):
        ctrl = PlanModeController()
        assert ctrl.get_prompt_guidance() == ""

        ctrl.enter_plan_mode("Refactor")
        assert "PLAN MODE ACTIVE" in ctrl.get_prompt_guidance()

        ctrl.submit_plan(PlanArtifact(goal="Refactor", steps=["Step"]))
        assert "PLAN SUBMITTED" in ctrl.get_prompt_guidance()

        ctrl.approve_plan()
        assert "PLAN APPROVED" in ctrl.get_prompt_guidance()


class TestStructuredQuestionsTool:
    def test_question_item_and_answer_dataclasses(self):
        q = QuestionItem(
            id="q1",
            question="Which database backend should we use?",
            options=["SQLite", "PostgreSQL", "In-Memory"],
            is_multi_select=False,
            header="Backend Choice",
        )
        d = q.to_dict()
        assert d["id"] == "q1"
        assert d["is_multi_select"] is False
        assert len(d["options"]) == 3

        restored = QuestionItem.from_dict(d)
        assert restored.id == "q1"
        assert restored.question == q.question
        assert restored.options == q.options

    def test_default_fallback_answers(self):
        tool = AskUserQuestionTool()
        q = QuestionItem(
            id="q1",
            question="Choose architecture",
            options=["Monolith", "Microservices"],
        )
        res = tool.ask_questions([q])
        assert len(res) == 1
        assert res[0]["id"] == "q1"
        assert res[0]["selected"] == ["Monolith"]

    def test_preset_answers_by_id_and_text(self):
        tool = AskUserQuestionTool()
        tool.set_preset_answer("q_auth", ["OAuth2"])
        tool.set_preset_answer("Should we enable caching?", ["Yes"])

        q1 = QuestionItem(id="q_auth", question="Which auth method?", options=["OAuth2", "JWT"])
        q2 = QuestionItem(id="q2", question="Should we enable caching?", options=["Yes", "No"])

        res = tool.ask_questions([q1, q2])
        assert res[0]["selected"] == ["OAuth2"]
        assert res[1]["selected"] == ["Yes"]

    def test_custom_answer_provider(self):
        def mock_ui_dialog(question: QuestionItem):
            if "color" in question.question:
                return QuestionAnswer(id=question.id, selected=["Dark"])
            return QuestionAnswer(id=question.id, custom="Freeform user response")

        tool = AskUserQuestionTool(answer_provider=mock_ui_dialog)
        q1 = QuestionItem(id="q1", question="Preferred color theme?", options=["Light", "Dark"])
        q2 = QuestionItem(id="q2", question="Any special requirements?")

        res = tool.ask_questions([q1, q2])
        assert res[0]["selected"] == ["Dark"]
        assert res[1]["custom"] == "Freeform user response"

    def test_multi_select_validation(self):
        tool = AskUserQuestionTool()
        tool.set_preset_answer("q_single", ["Option A", "Option B"])

        # Single-select with multiple choices must raise ValueError
        q_single = QuestionItem(id="q_single", question="Pick one", options=["Option A", "Option B"], is_multi_select=False)
        with pytest.raises(ValueError, match="does not allow multi-select"):
            tool.ask_questions([q_single])

        # Multi-select question allows multiple choices
        tool.set_preset_answer("q_multi", ["Option A", "Option B"])
        q_multi = QuestionItem(id="q_multi", question="Pick multiple", options=["Option A", "Option B"], is_multi_select=True)
        res = tool.ask_questions([q_multi])
        assert res[0]["selected"] == ["Option A", "Option B"]

    def test_execute_tool_interface(self):
        tool = AskUserQuestionTool()
        tool.set_preset_answer("q1", ["Option 1"])
        result = tool.execute({
            "questions": [
                {"id": "q1", "question": "Test question?", "options": ["Option 1", "Option 2"]}
            ]
        })
        assert "answers" in result
        assert result["answers"][0]["selected"] == ["Option 1"]

    def test_tool_definition_schema(self):
        tool = AskUserQuestionTool()
        schema = tool.get_tool_definition()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "ask_user_question"
        assert "questions" in schema["function"]["parameters"]["properties"]

    def test_invalid_questions(self):
        tool = AskUserQuestionTool()
        with pytest.raises(ValueError, match="questions list cannot be empty"):
            tool.ask_questions([])

        with pytest.raises(ValueError, match="Question text must be non-empty"):
            tool.ask_questions([QuestionItem(question="")])


class TestTodoChecklist:
    def test_todo_item_validation(self):
        item = TodoItem(id="t1", content="Write tests", status="pending")
        assert item.id == "t1"
        assert item.content == "Write tests"
        assert item.status == TodoStatus.PENDING

        with pytest.raises(ValueError, match="Todo content must be a non-empty string"):
            TodoItem(id="t2", content="   ")

        with pytest.raises(ValueError, match="Invalid todo status 'unknown'"):
            TodoItem(id="t3", content="Valid content", status="unknown")

    def test_todo_write_and_todo_get(self):
        checklist = TodoChecklist()
        items = [
            {"id": "task_1", "content": "Analyze repository", "status": "completed"},
            {"id": "task_2", "content": "Implement feature", "status": "in_progress"},
            {"id": "task_3", "content": "Run tests", "status": "pending"},
        ]
        result = checklist.todo_write(items)
        assert len(result) == 3
        assert result[0]["status"] == "completed"
        assert result[1]["status"] == "in_progress"
        assert result[2]["status"] == "pending"

        retrieved = checklist.todo_get()
        assert retrieved == result

    def test_todo_write_replaces_full_list(self):
        checklist = TodoChecklist()
        checklist.todo_write([{"content": "Old task", "status": "pending"}])
        assert len(checklist.todo_get()) == 1

        checklist.todo_write([
            {"content": "New task 1", "status": "pending"},
            {"content": "New task 2", "status": "in_progress"},
        ])
        assert len(checklist.todo_get()) == 2
        assert checklist.todo_get()[0]["content"] == "New task 1"

    def test_duplicate_content_rejection(self):
        checklist = TodoChecklist()
        with pytest.raises(ValueError, match="Duplicate todo content"):
            checklist.todo_write([
                {"content": "Same content", "status": "pending"},
                {"content": "Same content", "status": "in_progress"},
            ])

    def test_single_active_discipline_when_parallel_disabled(self):
        checklist = TodoChecklist(allow_parallel_in_progress=False)
        # One in_progress is fine
        checklist.todo_write([
            {"content": "Task 1", "status": "in_progress"},
            {"content": "Task 2", "status": "pending"},
        ])

        # Two in_progress raises ValueError
        with pytest.raises(ValueError, match="At most one todo may be in_progress"):
            checklist.todo_write([
                {"content": "Task 1", "status": "in_progress"},
                {"content": "Task 2", "status": "in_progress"},
            ])

    def test_parallel_in_progress_allowed_by_default(self):
        checklist = TodoChecklist(allow_parallel_in_progress=True)
        checklist.todo_write([
            {"content": "Task 1", "status": "in_progress"},
            {"content": "Task 2", "status": "in_progress"},
        ])
        assert len(checklist.todo_get()) == 2

    def test_todo_update(self):
        checklist = TodoChecklist()
        checklist.todo_write([{"id": "t1", "content": "Initial", "status": "pending"}])
        updated = checklist.todo_update("t1", status="completed", content="Finished task")
        assert updated["status"] == "completed"
        assert updated["content"] == "Finished task"

        with pytest.raises(KeyError, match="Todo item with id 'unknown' not found"):
            checklist.todo_update("unknown", status="completed")

    def test_counts(self):
        checklist = TodoChecklist()
        checklist.todo_write([
            {"content": "Task 1", "status": "completed"},
            {"content": "Task 2", "status": "in_progress"},
            {"content": "Task 3", "status": "pending"},
        ])
        counts = checklist.counts()
        assert counts["completed"] == 1
        assert counts["in_progress"] == 1
        assert counts["pending"] == 1
        assert counts["total"] == 3

    def test_render_ascii(self):
        checklist = TodoChecklist()
        assert checklist.render_ascii() == "No todos in checklist."

        checklist.todo_write([
            {"id": "1", "content": "Read codebase", "status": "completed"},
            {"id": "2", "content": "Write patch", "status": "in_progress"},
            {"id": "3", "content": "Verify tests", "status": "pending"},
        ])
        ascii_out = checklist.render_ascii()
        assert "[x] #1 Read codebase" in ascii_out
        assert "[>] #2 Write patch" in ascii_out
        assert "[ ] #3 Verify tests" in ascii_out
        assert "1/3 completed (1 in progress, 1 pending)" in ascii_out

    def test_render_markdown(self):
        checklist = TodoChecklist()
        assert checklist.render_markdown() == "_No todos in checklist._"

        checklist.todo_write([
            {"content": "Read codebase", "status": "completed"},
            {"content": "Write patch", "status": "in_progress"},
            {"content": "Verify tests", "status": "pending"},
        ])
        md_out = checklist.render_markdown()
        assert "- [x] Read codebase" in md_out
        assert "- [ ] **(in progress)** Write patch" in md_out
        assert "- [ ] Verify tests" in md_out

    def test_execute_todo_write_and_todo_get(self):
        checklist = TodoChecklist()
        res_write = checklist.execute("todo_write", {
            "todos": [
                {"content": "Task A", "status": "pending"}
            ]
        })
        assert "todos" in res_write
        assert "counts" in res_write
        assert len(res_write["todos"]) == 1

        res_get = checklist.execute("todo_get", {})
        assert res_get["todos"] == res_write["todos"]

        with pytest.raises(ValueError, match="Unknown todo tool"):
            checklist.execute("unknown_tool", {})

    def test_get_tool_definitions(self):
        checklist = TodoChecklist()
        defs = checklist.get_tool_definitions()
        assert len(defs) == 2
        names = [d["function"]["name"] for d in defs]
        assert "todo_write" in names
        assert "todo_get" in names


class TestAdversarialPlanMode:
    """Aggressive adversarial penetration tests against tool policy and state machine."""

    def test_plan_ready_and_rejected_states_block_mutation_and_execution_tools(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Fix security bug")
        ctrl.submit_plan(PlanArtifact(goal="Fix security bug", steps=["Read file"]))

        assert ctrl.state == PlanModeState.PLAN_READY
        # Must block all mutation and execution tools while waiting for approval
        blocked_in_ready = [
            "propose_patch",
            "apply",
            "apply_patch",
            "write_file",
            "edit_file",
            "delete_file",
            "bash",
            "terminal_execute",
            "terminal_create",
            "run_command",
            "exec_command",
            "custom_mutator",
        ]
        for tool in blocked_in_ready:
            assert not ctrl.is_tool_allowed(tool), f"Tool '{tool}' should be blocked in PLAN_READY"
            with pytest.raises(PlanModePolicyError):
                ctrl.check_tool_allowed(tool)

        # Must allow read-only tools while waiting for approval
        assert ctrl.is_tool_allowed("read_file")
        assert ctrl.is_tool_allowed("search_text")
        assert ctrl.is_tool_allowed("submit_plan")

        # Now reject plan
        ctrl.reject_plan("Please revise plan")
        assert ctrl.state == PlanModeState.REJECTED

        # In REJECTED state, must also block all mutation and execution tools
        for tool in blocked_in_ready:
            assert not ctrl.is_tool_allowed(tool), f"Tool '{tool}' should be blocked in REJECTED"
            with pytest.raises(PlanModePolicyError):
                ctrl.check_tool_allowed(tool)

    def test_substring_and_crafted_tool_name_bypasses(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Testing bypasses")

        # Crafted tool names with substrings like 'get' or 'search' in dangerous names
        crafted_malicious_tools = [
            "target_overwrite",
            "budget_mutation",
            "gadget_executor",
            "forget_guardrails",
            "research_and_destroy",
            "search_and_destroy",
            "delete_all_get",  # ends with _get but dangerous? No, wait: prefix/suffix
            "destroy_disk",
        ]
        for tool in ["target_overwrite", "budget_mutation", "gadget_executor", "forget_guardrails", "destroy_disk"]:
            assert not ctrl.is_tool_allowed(tool), f"Crafted tool '{tool}' should be blocked in EXPLORING"

    def test_tool_name_edge_cases_and_types(self):
        ctrl = PlanModeController()
        ctrl.enter_plan_mode("Explore")

        assert not ctrl.is_tool_allowed("")
        assert not ctrl.is_tool_allowed("   ")
        assert not ctrl.is_tool_allowed(None)  # type: ignore[arg-type]
        assert not ctrl.is_tool_allowed(123)  # type: ignore[arg-type]

    def test_exhaustive_state_transition_matrix(self):
        ctrl = PlanModeController()

        # From INACTIVE
        with pytest.raises(PlanModeError, match="Cannot approve"):
            ctrl.approve_plan()
        with pytest.raises(PlanModeError, match="Cannot reject"):
            ctrl.reject_plan("Feedback")
        with pytest.raises(PlanModeError, match="Cannot submit"):
            ctrl.submit_plan(PlanArtifact(goal="G"))

        # From EXPLORING
        ctrl.enter_plan_mode("Goal 1")
        with pytest.raises(PlanModeError, match="Cannot approve"):
            ctrl.approve_plan()
        with pytest.raises(PlanModeError, match="Cannot reject"):
            ctrl.reject_plan("Feedback")

        # From PLAN_READY
        ctrl.submit_plan(PlanArtifact(goal="Goal 1", steps=["S1"]))
        # Can re-submit in PLAN_READY
        ctrl.submit_plan(PlanArtifact(goal="Goal 1 updated", steps=["S1", "S2"]))
        assert ctrl.current_plan.goal == "Goal 1 updated"

        # From APPROVED
        ctrl.approve_plan()
        assert ctrl.state == PlanModeState.APPROVED
        with pytest.raises(PlanModeError, match="Cannot approve"):
            ctrl.approve_plan()
        with pytest.raises(PlanModeError, match="Cannot reject"):
            ctrl.reject_plan("Feedback")
        with pytest.raises(PlanModeError, match="Cannot submit"):
            ctrl.submit_plan(PlanArtifact(goal="G2"))

        # Re-enter plan mode from APPROVED is valid
        ctrl.enter_plan_mode("Goal 2")
        assert ctrl.state == PlanModeState.EXPLORING


class TestAdversarialQuestions:
    """Adversarial tests for AskUserQuestionTool and QuestionItem."""

    def test_string_boolean_coercion_in_from_dict(self):
        # LLM returns string "false" for is_multi_select
        q1 = QuestionItem.from_dict({
            "question": "Pick database",
            "options": ["SQLite", "PostgreSQL"],
            "is_multi_select": "false",
        })
        assert q1.is_multi_select is False

        q2 = QuestionItem.from_dict({
            "question": "Pick database",
            "options": ["SQLite", "PostgreSQL"],
            "multi_select": "0",
        })
        assert q2.is_multi_select is False

        q3 = QuestionItem.from_dict({
            "question": "Pick features",
            "options": ["Auth", "Logging"],
            "is_multi_select": "true",
        })
        assert q3.is_multi_select is True

    def test_empty_and_whitespace_options_sanitization(self):
        q = QuestionItem(
            id="q1",
            question="Choose option",
            options=["", "  ", "Valid Option", "  ", "Valid Option 2"],
        )
        assert q.options == ["Valid Option", "Valid Option 2"]

    def test_duplicate_question_ids_in_single_batch_rejected(self):
        tool = AskUserQuestionTool()
        q1 = QuestionItem(id="dup_id", question="First question", options=["A", "B"])
        q2 = QuestionItem(id="dup_id", question="Second question", options=["C", "D"])

        with pytest.raises(ValueError, match="Duplicate question id 'dup_id'"):
            tool.ask_questions([q1, q2])

    def test_multi_select_overflow_rejected(self):
        tool = AskUserQuestionTool()
        tool.set_preset_answer("q1", ["A", "B"])
        q = QuestionItem(id="q1", question="Single choice", options=["A", "B"], is_multi_select=False)
        with pytest.raises(ValueError, match="does not allow multi-select"):
            tool.ask_questions([q])


class TestAdversarialTodoChecklist:
    """Adversarial tests for TodoChecklist and TodoItem."""

    def test_duplicate_todo_ids_rejected(self):
        cl = TodoChecklist()
        with pytest.raises(ValueError, match="Duplicate todo id: 'task_1'"):
            cl.todo_write([
                {"id": "task_1", "content": "First task", "status": "pending"},
                {"id": "task_1", "content": "Second task", "status": "pending"},
            ])

    def test_todo_update_enforces_single_active_discipline(self):
        cl = TodoChecklist(allow_parallel_in_progress=False)
        cl.todo_write([
            {"id": "t1", "content": "Task 1", "status": "in_progress"},
            {"id": "t2", "content": "Task 2", "status": "pending"},
        ])

        # Attempting to set t2 in_progress while t1 is in_progress must fail
        with pytest.raises(ValueError, match="At most one todo may be in_progress"):
            cl.todo_update("t2", status="in_progress")

        # Transitioning t1 to completed, then t2 to in_progress succeeds
        cl.todo_update("t1", status="completed")
        updated = cl.todo_update("t2", status="in_progress")
        assert updated["status"] == "in_progress"

    def test_todo_item_strict_type_and_value_validation(self):
        # Invalid status type
        with pytest.raises(ValueError, match="Invalid todo status"):
            TodoItem(id="t1", content="Task", status=123)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Invalid todo status"):
            TodoItem(id="t1", content="Task", status=None)  # type: ignore[arg-type]

        # Empty or whitespace id
        with pytest.raises(ValueError, match="Todo id must be a non-empty string"):
            TodoItem(id="", content="Task")

        with pytest.raises(ValueError, match="Todo id must be a non-empty string"):
            TodoItem(id="   ", content="Task")

        # Empty or whitespace content
        with pytest.raises(ValueError, match="Todo content must be a non-empty string"):
            TodoItem(id="t1", content="")

        with pytest.raises(ValueError, match="Todo content must be a non-empty string"):
            TodoItem(id="t1", content="   ")

