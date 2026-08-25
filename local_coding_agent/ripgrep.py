"""Fast file search and content grep using ripgrep with stdlib fallback.

Adapted from DeepSeek Harness @deepseek-ai/dsh-tool-fs-search.
Provides RipgrepMatch structured results, glob filtering, regex and literal search,
and resilient stdlib traversal fallback when `rg` is not available on PATH.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RipgrepMatch:
    """A single match line found in a search."""

    file: str
    line_number: int
    line_content: str


IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".tox",
        ".local_agent",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


def _is_binary_file(file_path: Path) -> bool:
    """Check whether a file appears to be binary by inspecting initial bytes."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except OSError:
        return True


def _match_globs(rel_path_str: str, file_name: str, globs: list[str] | None) -> bool:
    """Check whether relative path and filename satisfy glob inclusion/exclusion rules."""
    if not globs:
        return True

    positive_globs = [g for g in globs if not g.startswith("!")]
    negative_globs = [g[1:] for g in globs if g.startswith("!")]

    # Check exclusions first
    for neg in negative_globs:
        if fnmatch.fnmatch(rel_path_str, neg) or fnmatch.fnmatch(file_name, neg):
            return False

    # Check positive globs if any are specified
    if positive_globs:
        matched = any(
            fnmatch.fnmatch(rel_path_str, pos) or fnmatch.fnmatch(file_name, pos)
            for pos in positive_globs
        )
        if not matched:
            return False

    return True


def _stdlib_search(
    query: str,
    root_path: Path,
    globs: list[str] | None = None,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
) -> list[RipgrepMatch]:
    """Pure-Python fallback for grep search across repository files."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if is_regex else re.escape(query), flags)
    except re.error:
        # Invalid regex fallback
        return []

    results: list[RipgrepMatch] = []

    if root_path.is_file():
        candidates = [root_path]
    else:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(root_path):
            # Prune ignored directory trees in-place
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRECTORIES]
            for fname in filenames:
                file_p = Path(dirpath) / fname
                candidates.append(file_p)

    for file_path in candidates:
        if not file_path.is_file():
            continue

        try:
            rel_path = file_path.relative_to(root_path)
            rel_str = rel_path.as_posix()
        except ValueError:
            rel_str = file_path.name

        if not _match_globs(rel_str, file_path.name, globs):
            continue

        if _is_binary_file(file_path):
            continue

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    if pattern.search(line):
                        clean_line = line.rstrip("\r\n")
                        results.append(
                            RipgrepMatch(
                                file=rel_str,
                                line_number=line_idx,
                                line_content=clean_line,
                            )
                        )
                        if len(results) >= max_results:
                            return results
        except OSError:
            continue

    return results


def _stdlib_files(
    pattern: str,
    root_path: Path,
    max_results: int = 100,
) -> list[str]:
    """Pure-Python fallback for listing matching files."""
    matched_files: list[str] = []
    has_pattern = bool(pattern and pattern != "*")

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRECTORIES]
        for fname in sorted(filenames):
            full_path = Path(dirpath) / fname
            try:
                rel_path = full_path.relative_to(root_path)
                rel_str = rel_path.as_posix()
            except ValueError:
                rel_str = full_path.name

            if has_pattern:
                if not (fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(fname, pattern)):
                    continue

            matched_files.append(rel_str)
            if len(matched_files) >= max_results:
                return matched_files

    return matched_files


def ripgrep_search(
    query: str,
    root: Path | str,
    globs: list[str] | None = None,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    _force_fallback: bool = False,
) -> list[RipgrepMatch]:
    """Perform a fast text or regex search across files under root.

    Uses `rg` when available on PATH, falling back to stdlib search seamlessly.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        return []

    rg_bin = None if _force_fallback else shutil.which("rg")

    if rg_bin:
        cmd: list[str] = [rg_bin, "--json"]
        if not case_sensitive:
            cmd.append("-i")
        else:
            cmd.append("-s")

        if not is_regex:
            cmd.append("-F")

        if globs:
            for g in globs:
                cmd.extend(["-g", g])

        cmd.extend(["--max-count", str(max_results)])
        cmd.extend(["--", query, str(root_path)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root_path if root_path.is_dir() else root_path.parent),
                check=False,
            )

            if proc.returncode in (0, 1):
                matches: list[RipgrepMatch] = []
                for line in proc.stdout.splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") == "match":
                        data = entry.get("data", {})
                        path_text = data.get("path", {}).get("text", "")
                        file_p = Path(path_text)
                        try:
                            rel_str = file_p.resolve().relative_to(root_path).as_posix()
                        except ValueError:
                            rel_str = file_p.name or path_text

                        line_num = data.get("line_number", 0)
                        line_text = data.get("lines", {}).get("text", "").rstrip("\r\n")
                        matches.append(
                            RipgrepMatch(
                                file=rel_str,
                                line_number=line_num,
                                line_content=line_text,
                            )
                        )
                        if len(matches) >= max_results:
                            break
                return matches
        except (OSError, subprocess.SubprocessError):
            pass

    return _stdlib_search(
        query=query,
        root_path=root_path,
        globs=globs,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        max_results=max_results,
    )


def ripgrep_files(
    pattern: str,
    root: Path | str,
    max_results: int = 100,
    _force_fallback: bool = False,
) -> list[str]:
    """Find files matching a pattern under root.

    Uses `rg --files` when available, falling back to stdlib search seamlessly.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        return []

    rg_bin = None if _force_fallback else shutil.which("rg")

    if rg_bin:
        cmd: list[str] = [rg_bin, "--files"]
        if pattern and pattern != "*":
            cmd.extend(["-g", pattern])
        cmd.extend(["--", str(root_path)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root_path if root_path.is_dir() else root_path.parent),
                check=False,
            )

            if proc.returncode in (0, 1):
                files: list[str] = []
                for line in proc.stdout.splitlines():
                    cleaned = line.strip()
                    if not cleaned:
                        continue
                    file_p = Path(cleaned)
                    try:
                        rel_str = file_p.resolve().relative_to(root_path).as_posix()
                    except ValueError:
                        rel_str = file_p.name or cleaned
                    files.append(rel_str)
                    if len(files) >= max_results:
                        break
                return files
        except (OSError, subprocess.SubprocessError):
            pass

    return _stdlib_files(
        pattern=pattern,
        root_path=root_path,
        max_results=max_results,
    )
