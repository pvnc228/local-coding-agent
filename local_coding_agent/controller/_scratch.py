"""Bounded private copies; never apply candidate bytes in the caller's checkout.

This isolates repository state, not arbitrary host-approved check commands from
the operating system. Links/reparse points are refused rather than retained:
an absolute link in a copy could otherwise write back into the source tree.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import os
from pathlib import Path
import shutil
import shlex
import stat
import subprocess
import tempfile
from threading import Event
import time
from typing import Any, Iterator

from ..repository_tools import BoundedRepositoryTools, ToolCancelled, ToolPolicyError
from ..prescriptions import redact_check_output
from ..task import TaskEnvelope
from ..validators import _normalize_task_path, apply_patch, parse_unified_diff
from ._post_apply import run_post_apply_checks

_EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.local-run', '.pytest_cache'}


def check_target_issues(task: TaskEnvelope, changed) -> list[str]:
    targets = set()
    for command in task.checks:
        try:
            for token in shlex.split(command, posix=False):
                path = token.strip('\"\'').split('::', 1)[0].replace('\\', '/').removeprefix('./')
                targets.add(os.path.normcase(path).replace('\\', '/'))
        except ValueError:
            continue
    protected = []
    for name in changed:
        normalized = os.path.normcase(name.replace('\\', '/')).replace('\\', '/')
        for target in targets:
            if target == '.':
                match = Path(name).name.startswith('test_') or Path(name).name.endswith('_test.py')
            else:
                match = (normalized == target or
                         normalized.startswith(target.rstrip('/') + '/') or
                         fnmatchcase(normalized, target))
            if match:
                protected.append(name)
                break
    return ['do not change an explicit check target: ' + ', '.join(protected)] if protected else []


def _cancel(event: Event | None) -> None:
    if event is not None and event.is_set():
        raise ToolCancelled('scratch verification cancelled')


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def _copy_names(root: Path, max_files: int) -> list[Path]:
    # Git includes dirty tracked bytes and nonignored untracked support files.
    # Spool listing output to disk, then bound its allocation before decoding.
    with tempfile.TemporaryFile() as listing:
        try:
            run = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', '-z'],
                                 cwd=root, stdout=listing, stderr=subprocess.DEVNULL, timeout=15)
        except FileNotFoundError:
            run = None
        if run is not None and run.returncode == 0:
            listing.seek(0)
            raw = listing.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('scratch listing size limit exceeded')
            names = {os.fsdecode(name) for name in raw.split(b'\0') if name}
            if len(names) > max_files:
                raise ValueError('scratch file count limit exceeded')
            return [Path(name) for name in sorted(names) if not _EXCLUDED.intersection(Path(name).parts)]
    names = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in _EXCLUDED]
        for name in dirs:
            if _linked(Path(directory) / name):
                raise ValueError('scratch copy refuses links and reparse points')
        for name in files:
            names.append((Path(directory) / name).relative_to(root))
            if len(names) > max_files:
                raise ValueError('scratch file count limit exceeded')
    return names


@contextmanager
def scratch_workspace(workspace: str | Path, *, cancel_event: Event | None = None,
                      audit: list[dict[str, Any]] | None = None,
                      max_files: int = 20_000, max_bytes: int = 256_000_000) -> Iterator[Path]:
    audit = audit if audit is not None else []
    root = Path(workspace).resolve(strict=True)
    target = Path(tempfile.mkdtemp(prefix='lca-scratch-'))
    try:
        _cancel(cancel_event)
        total = 0
        for relative in _copy_names(root, max_files):
            _cancel(cancel_event)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('scratch path escapes workspace')
            source = root / relative
            if not source.exists() and not source.is_symlink():
                continue  # tracked deletion
            for parent in (source, *source.parents):
                if parent == root:
                    break
                if _linked(parent):
                    raise ValueError('scratch copy refuses links and reparse points')
            if not source.exists():  # a tracked deletion belongs to the snapshot
                continue
            if not source.is_file():
                raise ValueError('scratch copy only supports regular files')
            before = source.lstat()
            if stat.S_ISLNK(before.st_mode) or getattr(before, 'st_file_attributes', 0) & 0x400:
                raise ValueError('scratch copy refuses links and reparse points')
            total += before.st_size
            if total > max_bytes:
                raise ValueError('scratch copy byte limit exceeded')
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as src, destination.open('wb') as dst:
                opened = os.fstat(src.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError('workspace path changed during scratch copy')
                copied = 0
                while block := src.read(256_000):
                    _cancel(cancel_event)
                    copied += len(block)
                    if copied > before.st_size:
                        raise ValueError('workspace changed during scratch copy')
                    dst.write(block)
            after = source.lstat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('workspace changed during scratch copy')
            shutil.copymode(source, destination)
        audit.append({'event': 'scratch_created', 'files': len(list(target.rglob('*'))), 'bytes': total})
        yield target
    finally:
        def writable(function, path, error):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            function(path)

        for attempt in range(3):
            try:
                shutil.rmtree(target, onerror=writable)
                audit.append({'event': 'scratch_cleanup'})
                break
            except OSError:
                if attempt == 2:
                    audit.append({'event': 'cleanup_failed', 'detail': 'scratch directory could not be removed'})
                else:
                    time.sleep(0.05)


class ScratchTools(BoundedRepositoryTools):
    def _run_tests(self, arguments):
        result = super()._run_tests(arguments)
        for name in ('stdout', 'stderr', 'evidence'):
            result[name] = redact_check_output(result.get(name, ''))
        return result

    def _isolated_environment(self) -> dict[str, str]:
        env = super()._isolated_environment()
        # Both common package layouts precede any parent editable installation.
        env['PYTHONPATH'] = os.pathsep.join((str(self.workspace_root), str(self.workspace_root / 'src')))
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return env


@dataclass
class ScratchVerdict:
    passed: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    state: str = 'unavailable'
    detail: str = ''


def verify_in_scratch(workspace: str | Path, patch: str, task: TaskEnvelope, *,
                      cancel_event: Event | None = None,
                      audit: list[dict[str, Any]] | None = None) -> ScratchVerdict:
    if not task.checks:
        return ScratchVerdict(detail='task has no checks')
    audit = audit if audit is not None else []
    changed, issues = parse_unified_diff(patch) if patch.strip() else ((), [])
    allowed = {_normalize_task_path(name) for name in task.files}
    if issues or check_target_issues(task, changed) or any(_normalize_task_path(name) not in allowed for name in changed):
        return ScratchVerdict(state='apply_failed', detail='patch is invalid or outside task allowlist')
    try:
        with scratch_workspace(workspace, cancel_event=cancel_event, audit=audit) as scratch:
            _cancel(cancel_event)
            if patch.strip():
                applied, detail = apply_patch(scratch, patch)
                if not applied:
                    return ScratchVerdict(state='apply_failed', detail=detail[:500])
            checks, passed = run_post_apply_checks(
                task, ScratchTools(scratch, task, cancel_event=cancel_event),
                active_cancel=cancel_event, audit=audit)
            audit.append({'event': 'scratch_verified', 'passed': passed})
            return ScratchVerdict(passed, checks, 'passed' if passed else 'failed')
    except ToolCancelled:
        return ScratchVerdict(state='cancelled', detail='task was cancelled')
    except (ValueError, OSError, ToolPolicyError, subprocess.TimeoutExpired):
        return ScratchVerdict(detail='scratch copy or check unavailable; inspect resource limits and check policy')
