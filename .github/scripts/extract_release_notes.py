#!/usr/bin/env python3
"""Extract one version's section from CHANGELOG.md for GitHub Release notes.

Usage: extract_release_notes.py <version> <output-file>
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract_release_notes.py <version> <output-file>", file=sys.stderr)
        return 1
    version, output = sys.argv[1], Path(sys.argv[2])

    changelog_path = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")

    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.S | re.M,
    )
    section = match.group(1).strip() if match else (
        f"Changelog section for {version} not found; see CHANGELOG.md."
    )

    body = f"# Release v{version}\n\n{section}\n"
    output.write_text(body, encoding="utf-8", newline="\n")
    print(f"wrote {output} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
