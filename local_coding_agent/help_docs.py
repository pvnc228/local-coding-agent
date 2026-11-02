"""Help documents shipped inside the package, listed from disk on every call.

Nothing here names a document. Every ``*.md`` file in ``local_coding_agent/docs``
is a Help page: the slug is the lower-cased file name, the title is the first
``# `` heading, and an optional comment near the top picks the section and order:

    <!-- help: section="Connect" order=2 -->

Files without the comment land in the ``Docs`` section, sorted by title.
"""

from __future__ import annotations

import re
from importlib import metadata as importlib_metadata
from importlib import resources
from typing import Any

from . import __version__

MAX_DOC_BYTES = 512 * 1024
DEFAULT_SECTION = "Docs"
_DEFAULT_ORDER = 10_000
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COMMENT_RE = re.compile(r"<!--\s*help:(.*?)-->[ \t]*\r?\n?", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))')
_PACKAGE_NAME = "local-coding-agent"


def _docs_root() -> Any:
    return resources.files("local_coding_agent").joinpath("docs")


def _read_limited(entry: Any) -> str | None:
    """Return the file text, or None when it is unreadable or over the size limit."""
    try:
        with entry.open("rb") as handle:
            data = handle.read(MAX_DOC_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_DOC_BYTES:
        return None
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")


def _parse(text: str) -> tuple[dict[str, str], str]:
    """Split the optional help comment (first 5 lines) from the document body."""
    head = "\n".join(text.split("\n", 5)[:5])
    match = _COMMENT_RE.search(head)
    if not match:
        return {}, text
    attrs = {key: quoted or bare for key, quoted, bare in _ATTR_RE.findall(match.group(1))}
    return attrs, text[: match.start()] + text[match.end() :]


def _title(body: str, fallback: str) -> str:
    in_fence = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _order(raw: str | None) -> int:
    try:
        return int(raw) if raw is not None else _DEFAULT_ORDER
    except ValueError:
        return _DEFAULT_ORDER


def list_help_docs() -> list[dict[str, Any]]:
    """Enumerate the documents folder now; the result is also the slug whitelist."""
    docs: dict[str, dict[str, Any]] = {}
    try:
        entries = sorted(_docs_root().iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if not name.lower().endswith(".md") or not entry.is_file():
            continue
        stem = name[:-3]
        slug = stem.lower()
        if slug in docs or not _SLUG_RE.match(slug):
            continue
        text = _read_limited(entry)
        if text is None:
            continue
        attrs, body = _parse(text)
        docs[slug] = {
            "slug": slug,
            "title": _title(body, stem),
            "section": attrs.get("section", "").strip() or DEFAULT_SECTION,
            "order": _order(attrs.get("order")),
            "content": body,
        }
    return list(docs.values())


def help_index() -> dict[str, Any]:
    """Sections (ordered by their lowest document order) and the About facts."""
    sections: dict[str, list[dict[str, Any]]] = {}
    for doc in list_help_docs():
        sections.setdefault(doc["section"], []).append(doc)
    ordered = sorted(
        sections.items(),
        key=lambda item: (min(doc["order"] for doc in item[1]), item[0].lower()),
    )
    return {
        "sections": [
            {
                "name": name,
                "docs": [
                    {"slug": doc["slug"], "title": doc["title"]}
                    for doc in sorted(docs, key=lambda doc: (doc["order"], doc["title"].lower(), doc["slug"]))
                ],
            }
            for name, docs in ordered
        ],
        "about": about_info(),
    }


def read_help_doc(slug: str) -> dict[str, Any] | None:
    """One document by slug; only slugs that the current listing contains resolve."""
    for doc in list_help_docs():
        if doc["slug"] == slug:
            return {key: doc[key] for key in ("slug", "title", "section", "content")}
    return None


def _http_url(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if re.match(r"^https?://[^\s\"'<>]+$", value, re.IGNORECASE) else None


def about_info() -> dict[str, Any]:
    """Version from the package, license and links from its installed metadata."""
    info: dict[str, Any] = {
        "name": "Local Coding Agent",
        "version": __version__,
        "license": None,
        "homepage": None,
        "changelog": None,
    }
    try:
        meta = importlib_metadata.metadata(_PACKAGE_NAME)
    except importlib_metadata.PackageNotFoundError:
        return info
    info["license"] = (meta.get("License-Expression") or meta.get("License") or "").strip() or None
    urls: dict[str, str] = {}
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        safe = _http_url(url)
        if safe:
            urls[label.strip().lower()] = safe
    info["homepage"] = urls.get("homepage") or urls.get("repository") or urls.get("source")
    info["changelog"] = urls.get("changelog")
    return info
