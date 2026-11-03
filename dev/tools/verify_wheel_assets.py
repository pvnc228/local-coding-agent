"""Verify that a built wheel contains the desktop frontend assets and the Help documents."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


REQUIRED_ASSETS = frozenset(
    {
        "local_coding_agent/desktop/assets/tailwind.css",
        "local_coding_agent/desktop/assets/lucide.min.js",
    }
)


DOCS_DIR = Path(__file__).resolve().parents[2] / "local_coding_agent" / "docs"


def packaged_docs() -> frozenset[str]:
    """Every Help document in the source tree; the list is read from disk, never kept here."""

    return frozenset(f"local_coding_agent/docs/{path.name}" for path in DOCS_DIR.glob("*.md"))


def verify_wheel(path: Path) -> list[str]:
    """Return missing asset and Help document paths from one wheel, or raise on invalid input."""

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    return sorted((REQUIRED_ASSETS | packaged_docs()) - names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="Directory containing the built wheel")
    args = parser.parse_args(argv)

    wheels = sorted(args.dist.glob("*.whl"))
    if not wheels:
        parser.error(f"no wheel found in {args.dist}")
    if not packaged_docs():
        parser.error(f"no Help documents found in {DOCS_DIR}")

    failed = False
    for wheel in wheels:
        missing = verify_wheel(wheel)
        if missing:
            failed = True
            print(f"{wheel}: missing wheel files: {', '.join(missing)}", file=sys.stderr)
        else:
            print(f"{wheel}: desktop assets and help documents present")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
