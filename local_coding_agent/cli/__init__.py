"""Command-line entry point for local-coding-agent.

Refactored from a single module into a package. The full public API is
re-exported here so existing imports keep working.
"""

from ._main import main
from ._parser import build_parser
from ._dispatch import handle_subcommand
from ._input import load_task_input, load_task_file

__all__ = ["main", "build_parser", "handle_subcommand", "load_task_input", "load_task_file"]
