"""Independent validation of the model's structured candidate result."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .task import TaskEnvelope


def _fold_path(path: str) -> str:
    # ponytail: treat only Windows as case-insensitive; macOS default is
    # case-insensitive but this keeps the common Linux case strict.
    return path.casefold() if os.name == "nt" else path


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    changed_files: tuple[str, ...]
    issues: tuple[str, ...]
    resolved_patch: str = ""


def _split_edit_lines(text: str) -> list[str]:
    if text == "":
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _build_edit_diff(
    path: str,
    search: str,
    replace: str,
    start_line: int,
    old_no_newline: bool,
    new_no_newline: bool,
) -> str:
    """Build a minimal single-file unified diff replacing ``search`` with ``replace``.

    ``search`` is a line-aligned block already located by the caller; the diff
    is controller-owned, so the model never computes line numbers or hunk
    headers. ``old_no_newline`` marks the removed block's last line as lacking
    a trailing newline in the file (i.e. it reaches EOF without one).
    """

    old_lines = _split_edit_lines(search)
    new_lines = _split_edit_lines(replace)
    header = f"@@ -{start_line},{len(old_lines)} +{start_line},{len(new_lines)} @@"
    body: list[str] = []
    for position, line in enumerate(old_lines):
        body.append("-" + line)
        if position == len(old_lines) - 1 and old_no_newline:
            body.append("\\ No newline at end of file")
    for position, line in enumerate(new_lines):
        body.append("+" + line)
        if position == len(new_lines) - 1 and new_no_newline:
            body.append("\\ No newline at end of file")
    hunk = "\n".join([header, *body]) if body else header
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"{hunk}\n"
    )


def resolve_edits(
    workspace_root: str | Path,
    edits: Any,
    *,
    allowed_files: set[str],
    max_files: int,
    max_patch_bytes: int,
) -> tuple[str, tuple[str, ...], list[str]]:
    """Validate SEARCH/REPLACE edit blocks and synthesize one unified diff.

    Returns ``(patch, changed_files, issues)``. An edit block is
    ``{"file": ..., "search": ..., "replace": ...}``. ``search`` must match the
    current file content exactly once and be line-aligned, so a model only has
    to copy existing code instead of computing line numbers.
    """

    issues: list[str] = []
    changed: list[str] = []
    diff_parts: list[str] = []
    root = Path(workspace_root)
    seen: set[str] = set()
    edits_by_file: dict[str, list[dict[str, Any]]] = {}

    for edit in edits:
        if not isinstance(edit, Mapping):
            issues.append("each edit must be an object")
            continue
        raw_path = edit.get("file")
        search = edit.get("search")
        replace = edit.get("replace")
        if not isinstance(raw_path, str) or not raw_path.strip():
            issues.append("edit file must be a non-empty string")
            continue
        normalized, path_issue = _normalize_diff_path(raw_path)
        if path_issue:
            issues.append(path_issue)
            continue
        if normalized is None:
            issues.append(f"edit file path is empty: {raw_path}")
            continue
        if _fold_path(normalized) not in allowed_files:
            issues.append(f"edit file is outside task allowlist: {raw_path}")
            continue
        if not isinstance(search, str) or not search:
            issues.append("edit search must be a non-empty string")
            continue
        if not isinstance(replace, str):
            issues.append("edit replace must be a string")
            continue
        seen.add(_fold_path(normalized))
        edits_by_file.setdefault(normalized, []).append(
            {"raw_path": raw_path, "search": search, "replace": replace}
        )

    for normalized, file_edits in edits_by_file.items():
        target = root / Path(normalized)
        if not target.is_file():
            issues.append(f"edit file does not exist: {file_edits[0]['raw_path']}")
            continue
        try:
            original_content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"edit file is not UTF-8 text: {file_edits[0]['raw_path']}")
            continue

        if len(file_edits) == 1:
            e = file_edits[0]
            search = e["search"]
            replace = e["replace"]
            raw_path = e["raw_path"]
            count = original_content.count(search)
            if count == 0 and "\\n" in search:
                candidate_search = search.replace("\\r\\n", "\n").replace("\\n", "\n")
                if original_content.count(candidate_search) > 0:
                    search = candidate_search
                    count = original_content.count(search)
                    if "\\n" in replace:
                        replace = replace.replace("\\r\\n", "\n").replace("\\n", "\n")
            if count == 0:
                issues.append(f"edit search block not found: {raw_path}")
                continue
            if count > 1:
                issues.append(f"edit search block is ambiguous ({count} matches): {raw_path}")
                continue
            index = original_content.find(search)
            end = index + len(search)
            is_start_aligned = index == 0 or original_content[index - 1] == "\n"
            is_end_aligned = (
                end == len(original_content)
                or search.endswith("\n")
                or original_content[end] == "\n"
                or (original_content[end] == "\r" and end + 1 < len(original_content) and original_content[end + 1] == "\n")
            )
            if not (is_start_aligned and is_end_aligned):
                line_start = original_content.rfind("\n", 0, index) + 1 if index > 0 else 0
                line_end = original_content.find("\n", end)
                if line_end == -1:
                    line_end = len(original_content)
                prefix = original_content[line_start:index]
                suffix = original_content[end:line_end]
                search = original_content[line_start:line_end]
                replace = prefix + replace + suffix
                index = line_start
                end = line_end

            start_line = original_content[:index].count("\n") + 1
            old_no_newline = end == len(original_content) and not original_content.endswith("\n")
            new_no_newline = end == len(original_content) and bool(replace) and not replace.endswith("\n")
            try:
                diff_parts.append(
                    _build_edit_diff(
                        normalized,
                        search,
                        replace,
                        start_line,
                        old_no_newline,
                        new_no_newline,
                    )
                )
                changed.append(normalized)
            except ValueError as error:
                issues.append(str(error))
                continue
        else:
            current_content = original_content
            file_ok = True
            for e in file_edits:
                search = e["search"]
                replace = e["replace"]
                raw_path = e["raw_path"]
                count = current_content.count(search)
                if count == 0 and "\\n" in search:
                    candidate_search = search.replace("\\r\\n", "\n").replace("\\n", "\n")
                    if current_content.count(candidate_search) > 0:
                        search = candidate_search
                        count = current_content.count(search)
                        if "\\n" in replace:
                            replace = replace.replace("\\r\\n", "\n").replace("\\n", "\n")
                if count == 0:
                    issues.append(f"edit search block not found: {raw_path}")
                    file_ok = False
                    break
                if count > 1:
                    issues.append(f"edit search block is ambiguous ({count} matches): {raw_path}")
                    file_ok = False
                    break
                current_content = current_content.replace(search, replace, 1)
            if file_ok:
                diff_lines = list(
                    difflib.unified_diff(
                        original_content.splitlines(keepends=True),
                        current_content.splitlines(keepends=True),
                        fromfile=f"a/{normalized}",
                        tofile=f"b/{normalized}",
                    )
                )
                if diff_lines:
                    diff_parts.append(f"diff --git a/{normalized} b/{normalized}\n" + "".join(diff_lines))
                    changed.append(normalized)

    if len(seen) > max_files:
        issues.append(f"edits exceed max_patch_files={max_files}")
    if issues:
        return "", tuple(sorted(set(changed))), issues
    patch = "\n".join(diff_parts)
    if len(patch.encode("utf-8")) > max_patch_bytes:
        issues.append(f"edits exceed max_patch_bytes={max_patch_bytes}")
        return "", tuple(sorted(set(changed))), issues
    return patch, tuple(sorted(set(changed))), []


def _evidence_facts(evidence: str | None) -> dict[str, str | None]:
    """Extract security-relevant facts from an evidence string.

    The evidence is a `key=value; ` segment list produced by
    `_process_evidence` (e.g. `exit_code=0; passed=True; stdout_bytes=...`).
    Only `exit_code` and `passed` matter for anti-fabrication; the byte-count
    metadata may be rephrased by the model. Missing keys are parsed as None.

    Fallback: if neither side parses an `exit_code`, the comparison falls back
    to `passed` equality (already enforced separately as the core gate), so a
    model that rephrases evidence without structural values is not falsely
    rejected as long as `passed` matches.
    """

    facts: dict[str, str | None] = {"exit_code": None, "passed": None}
    if not evidence:
        return facts
    for segment in evidence.split(";"):
        if "=" not in segment:
            continue
        key, _, value = segment.partition("=")
        key = key.strip()
        value = value.strip()
        if key in facts and value:
            facts[key] = value
    return facts


def validate_candidate(
    candidate: Mapping[str, Any],
    task: TaskEnvelope,
    *,
    max_patch_bytes: int = 32_000,
    max_patch_files: int = 2,
    observed_checks: Mapping[str, Mapping[str, Any]] | None = None,
    workspace_root: str | Path | None = None,
) -> ValidationReport:
    issues: list[str] = []
    changed_files: tuple[str, ...] = ()
    if not isinstance(candidate, Mapping):
        return ValidationReport(False, (), ("candidate must be a JSON object",))

    if candidate.get("status") != "candidate":
        issues.append("status must be 'candidate'")
    if not isinstance(candidate.get("summary"), str) or not candidate["summary"].strip():
        issues.append("summary must be a non-empty string")
    patch = candidate.get("patch")
    edits = candidate.get("edits")
    if patch is None:
        patch = ""
    if not isinstance(patch, str):
        issues.append("patch must be a string")
        patch = ""
    if edits is not None and not isinstance(edits, list):
        issues.append("edits must be a list")
        edits = None
    checks = candidate.get("checks")
    if not isinstance(checks, list):
        issues.append("checks must be a list")
        checks = []
    if not isinstance(candidate.get("risks"), list):
        issues.append("risks must be a list")

    resolved_patch = ""
    has_patch = bool(patch.strip()) if isinstance(patch, str) else False
    has_edits = bool(edits) if isinstance(edits, list) else False
    if has_patch and has_edits:
        issues.append("candidate must provide either patch or edits, not both")
    elif has_edits:
        if workspace_root is None:
            issues.append("edits require a workspace to resolve search blocks")
        else:
            allowed = {_normalize_task_path(path) for path in task.files}
            resolved_patch, changed_files, edit_issues = resolve_edits(
                workspace_root,
                edits,
                allowed_files=allowed,
                max_files=max_patch_files,
                max_patch_bytes=max_patch_bytes,
            )
            issues.extend(edit_issues)
            if not edit_issues:
                patch = resolved_patch

    if patch:
        if max_patch_bytes <= 0 or len(patch.encode("utf-8")) > max_patch_bytes:
            issues.append(f"patch exceeds max_patch_bytes={max_patch_bytes}")
        changed_files, diff_issues = parse_unified_diff(patch)
        issues.extend(diff_issues)
        if len(changed_files) > max_patch_files:
            issues.append(f"patch exceeds max_patch_files={max_patch_files}")
        allowed = {_normalize_task_path(path) for path in task.files}
        for path in changed_files:
            if _fold_path(path) not in allowed:
                issues.append(f"patch file is outside task allowlist: {path}")
        if workspace_root is not None and not issues:
            applies, detail = check_patch_applies(workspace_root, patch)
            if not applies:
                issues.append(f"patch does not apply cleanly: {detail}")

    expected_checks = set(task.checks)
    seen_checks: set[str] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            issues.append("each check must be an object")
            continue
        command = check.get("command")
        if not isinstance(command, str) or not command:
            issues.append("check command must be a non-empty string")
            continue
        if command not in expected_checks:
            issues.append(f"check command is not allowlisted: {command}")
        if command in seen_checks:
            issues.append(f"duplicate check command: {command}")
        seen_checks.add(command)
        if not isinstance(check.get("passed"), bool):
            issues.append(f"check passed must be boolean: {command}")
        elif check["passed"] is False:
            issues.append(f"check failed: {command}")
        evidence = check.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            issues.append(f"check evidence is required: {command}")
        if observed_checks is None or command not in observed_checks:
            issues.append(f"check has no external runner evidence: {command}")
        else:
            observed = observed_checks[command]
            if check.get("passed") != observed.get("passed"):
                issues.append(f"check result disagrees with external runner: {command}")
            candidate_facts = _evidence_facts(check.get("evidence"))
            observed_facts = _evidence_facts(observed.get("evidence"))
            if (
                candidate_facts.get("exit_code") is not None
                and candidate_facts.get("exit_code") != observed_facts.get("exit_code")
            ):
                issues.append(f"check evidence disagrees with external runner: {command}")
            elif (
                candidate_facts.get("passed") is not None
                and candidate_facts.get("passed") != observed_facts.get("passed")
            ):
                issues.append(f"check evidence disagrees with external runner: {command}")

    if not (patch or resolved_patch or has_edits):
        missing_checks = expected_checks - seen_checks
        for command in sorted(missing_checks):
            issues.append(f"missing check evidence: {command}")
            if observed_checks is None or command not in observed_checks:
                issues.append(f"check has no external runner evidence: {command}")

    return ValidationReport(not issues, changed_files, tuple(issues), resolved_patch)


def check_patch_applies(
    workspace_root: str | Path,
    patch: str,
    *,
    timeout_seconds: float = 10,
) -> tuple[bool, str]:
    """Check a patch with git without modifying the workspace."""

    return _run_git_apply(workspace_root, patch, check=True, timeout_seconds=timeout_seconds)


def apply_patch(
    workspace_root: str | Path,
    patch: str,
    *,
    timeout_seconds: float = 10,
    reverse: bool = False,
) -> tuple[bool, str]:
    """Apply an already-validated patch to the workspace.

    This is the controller-only mediated-apply seam. The local model never
    reaches it directly: it has no `apply_patch` tool, and the controller only
    calls this after `validate_candidate` has accepted the proposal.
    """

    return _run_git_apply(
        workspace_root,
        patch,
        check=False,
        reverse=reverse,
        timeout_seconds=timeout_seconds,
    )


def _run_git_apply(
    workspace_root: str | Path,
    patch: str,
    *,
    check: bool,
    reverse: bool = False,
    timeout_seconds: float,
) -> tuple[bool, str]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    executable = shutil.which("git")
    if executable is None:
        return False, "git executable is unavailable"
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        return False, f"workspace directory does not exist: {root}"
    command = [executable, "apply", "--unidiff-zero", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    if reverse:
        command.append("--reverse")
    command.append("-")

    def run(text: str) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"git apply{' check' if check else ''} failed: {error}"
        if completed.returncode == 0:
            return True, ""
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return False, detail or f"git apply exited with code {completed.returncode}"

    applied, detail = run(patch)
    if applied:
        return True, ""
    # Git converts line endings only when core.autocrlf or attributes ask for it,
    # so an LF patch fits a CRLF file on Git for Windows but not on Linux or macOS.
    # ponytail: the retry can double the wait to 2 x timeout_seconds.
    crlf_patch = _crlf_hunks_for_crlf_targets(root, patch)
    if crlf_patch == patch:
        return False, detail
    retried, _ = run(crlf_patch)
    return (True, "") if retried else (False, detail)


def _uses_crlf(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return b"\r\n" in data and data.count(b"\r\n") == data.count(b"\n")


def _crlf_hunks_for_crlf_targets(root: Path, patch: str) -> str:
    """Give hunk lines CRLF endings where the target file uses CRLF throughout."""

    lines = patch.split("\n")
    crlf = False
    old_left = new_left = 0
    for index, line in enumerate(lines):
        if old_left > 0 or new_left > 0:
            if line.startswith("\\"):
                continue
            old_left -= int(line.startswith((" ", "-")))
            new_left -= int(line.startswith((" ", "+")))
            if crlf and not line.endswith("\r"):
                lines[index] = line + "\r"
        elif line.startswith("--- "):
            name = line[4:].rstrip("\r").split("\t", 1)[0]
            name = name[2:] if name.startswith("a/") else name
            crlf = name != "/dev/null" and _uses_crlf(root / name)
        elif line.startswith("@@ "):
            match = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            if match:
                old_left = int(match.group(1) or "1")
                new_left = int(match.group(2) or "1")
    return "\n".join(lines)


def parse_unified_diff(patch: str) -> tuple[tuple[str, ...], list[str]]:
    paths: list[str] = []
    issues: list[str] = []
    saw_old = False
    saw_new = False
    saw_hunk = False
    hunk: dict[str, int] | None = None

    def finish_hunk() -> None:
        nonlocal hunk
        if hunk is None:
            return
        # Over-declared hunks (the header promises more lines than the body has)
        # are not flagged here: git apply --check rejects them as "corrupt patch".
        # Lines past the header counts are flagged, because git drops them silently.
        hunk = None

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            finish_hunk()
            match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
            if not match:
                issues.append("patch has invalid diff header")
                continue
            for raw_path in match.groups():
                normalized, path_issue = _normalize_diff_path(raw_path)
                if path_issue:
                    issues.append(path_issue)
                elif normalized is not None and normalized not in paths:
                    paths.append(normalized)
        elif line.startswith("@@ "):
            finish_hunk()
            saw_hunk = True
            match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*", line)
            if not match:
                issues.append("patch has invalid hunk header")
                continue
            hunk = {
                "old_expected": int(match.group(2) or "1"),
                "new_expected": int(match.group(4) or "1"),
                "old_seen": 0,
                "new_seen": 0,
            }
        elif hunk is not None and (
            hunk["old_seen"] < hunk["old_expected"] or hunk["new_seen"] < hunk["new_expected"]
        ):
            # Inside a hunk git consumes lines by the header counts, so "---" and
            # "+++" are hunk content here, not file headers.
            if line.startswith("\\"):
                continue
            takes_old = line.startswith((" ", "-"))
            takes_new = line.startswith((" ", "+"))
            if not (takes_old or takes_new):
                issues.append("patch has invalid hunk line")
            elif (takes_old and hunk["old_seen"] >= hunk["old_expected"]) or (
                takes_new and hunk["new_seen"] >= hunk["new_expected"]
            ):
                issues.append("patch has lines outside hunk counts")
            else:
                hunk["old_seen"] += int(takes_old)
                hunk["new_seen"] += int(takes_new)
        elif line.startswith("--- "):
            saw_old = True
            normalized, path_issue = _normalize_diff_path(line[4:].split("\t", 1)[0].strip())
            if path_issue:
                issues.append(path_issue)
            elif normalized is not None and normalized not in paths:
                paths.append(normalized)
        elif line.startswith("+++ "):
            saw_new = True
            normalized, path_issue = _normalize_diff_path(line[4:].split("\t", 1)[0].strip())
            if path_issue:
                issues.append(path_issue)
            elif normalized is not None and normalized not in paths:
                paths.append(normalized)
        elif line[:1] in {" ", "+", "-"}:
            issues.append("patch has lines outside hunk counts")
    finish_hunk()
    if not paths or not saw_old or not saw_new or not saw_hunk:
        issues.append("patch is not a unified diff")
    return tuple(sorted(paths)), issues


def _normalize_diff_path(raw_path: str) -> tuple[str | None, str | None]:
    if raw_path == "/dev/null":
        return None, None
    path = raw_path[2:] if raw_path[:2] in {"a/", "b/"} else raw_path
    path = path.replace("\\", "/")
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return None, f"patch path is absolute or empty: {raw_path}"
    parts = [part for part in path.split("/") if part not in {"", "."}]
    if ".." in parts:
        return None, f"patch path escapes workspace: {raw_path}"
    return "/".join(parts), None


def _normalize_task_path(path: str) -> str:
    return _fold_path(path.replace("\\", "/").strip("/"))
