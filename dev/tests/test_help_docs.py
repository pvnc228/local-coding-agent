"""Help section: documents are listed from the packaged docs folder on every request."""

import contextlib
import http.client
import importlib.util
import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from local_coding_agent import help_docs
from local_coding_agent.cli import build_parser, handle_subcommand
from local_coding_agent.desktop.server import DesktopServer

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DOCS = ROOT / "local_coding_agent" / "docs"


@pytest.fixture
def docs_root(tmp_path, monkeypatch):
    """A temporary docs folder standing in for the packaged one."""
    root = tmp_path / "docs"
    root.mkdir()
    monkeypatch.setattr(help_docs, "_docs_root", lambda: root)
    return root


def _slugs(index):
    return [doc["slug"] for section in index["sections"] for doc in section["docs"]]


def test_new_markdown_file_appears_without_code_changes(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha guide\n\ntext\n", encoding="utf-8")
    assert _slugs(help_docs.help_index()) == ["alpha"]

    (docs_root / "Beta_Notes.md").write_text("# Beta notes\n", encoding="utf-8")
    (docs_root / "readme.txt").write_text("not a document", encoding="utf-8")
    index = help_docs.help_index()
    assert sorted(_slugs(index)) == ["alpha", "beta_notes"]
    assert {doc["title"] for section in index["sections"] for doc in section["docs"]} == {"Alpha guide", "Beta notes"}


def test_content_is_read_on_every_request(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha\n\nfirst\n", encoding="utf-8")
    assert "first" in help_docs.read_help_doc("alpha")["content"]
    (docs_root / "alpha.md").write_text("# Alpha\n\nsecond\n", encoding="utf-8")
    assert "second" in help_docs.read_help_doc("alpha")["content"]


def test_help_comment_sets_section_and_order(docs_root):
    (docs_root / "late.md").write_text('<!-- help: section="Reference" order=9 -->\n# Late\n', encoding="utf-8")
    (docs_root / "first.md").write_text('<!-- help: section="Start here" order=1 -->\n# First\n', encoding="utf-8")
    (docs_root / "second.md").write_text('<!-- help: section="Start here" order=2 -->\n# Second\n', encoding="utf-8")
    (docs_root / "zeta.md").write_text("# Zeta\n", encoding="utf-8")
    (docs_root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")

    sections = help_docs.help_index()["sections"]
    # Sections follow their lowest order; documents without a comment land in "Docs", sorted by title, last.
    assert [s["name"] for s in sections] == ["Start here", "Reference", "Docs"]
    assert [d["slug"] for d in sections[0]["docs"]] == ["first", "second"]
    assert [d["slug"] for d in sections[2]["docs"]] == ["alpha", "zeta"]


def test_title_falls_back_to_file_name_and_help_comment_is_not_served(docs_root):
    (docs_root / "no_heading.md").write_text('<!-- help: section="Connect" order=3 -->\njust text\n', encoding="utf-8")
    doc = help_docs.read_help_doc("no_heading")
    assert doc["title"] == "no_heading"
    assert doc["section"] == "Connect"
    assert "help:" not in doc["content"]
    assert "just text" in doc["content"]


def test_unknown_and_path_like_slugs_are_refused(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (docs_root.parent / "secret.md").write_text("# Secret\n", encoding="utf-8")
    for slug in ("missing", "../secret", "..", "alpha.md", "alpha/", "ALPHA ", "", "a/b", "..\\secret"):
        assert help_docs.read_help_doc(slug) is None, slug


def test_oversized_file_is_neither_listed_nor_served(docs_root, monkeypatch):
    monkeypatch.setattr(help_docs, "MAX_DOC_BYTES", 64)
    (docs_root / "big.md").write_text("# Big\n" + "x" * 200, encoding="utf-8")
    (docs_root / "small.md").write_text("# Small\n", encoding="utf-8")
    assert _slugs(help_docs.help_index()) == ["small"]
    assert help_docs.read_help_doc("big") is None


def test_every_packaged_document_is_listed_with_a_section():
    index = help_docs.help_index()
    on_disk = sorted(p.stem.lower() for p in PACKAGED_DOCS.glob("*.md"))
    assert sorted(_slugs(index)) == on_disk
    assert "quickstart" in on_disk and "faq" in on_disk and "roadmap" in on_disk
    # The shipped documents all declare where they belong.
    assert "Docs" not in [section["name"] for section in index["sections"]]
    for slug in on_disk:
        assert help_docs.read_help_doc(slug)["content"].strip()


def test_about_reads_version_and_metadata_not_literals(monkeypatch):
    import local_coding_agent

    class FakeMetadata:
        data = {"Name": "local-coding-agent", "License": "MIT"}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def get_all(self, key, default=None):
            if key == "Project-URL":
                return [
                    "Homepage, https://github.com/example/repo",
                    "Changelog, https://github.com/example/repo/blob/main/CHANGELOG.md",
                    "Evil, javascript:alert(1)",
                ]
            return default

    monkeypatch.setattr(help_docs.importlib_metadata, "metadata", lambda name: FakeMetadata())
    about = help_docs.about_info()
    assert about["version"] == local_coding_agent.__version__
    assert about["license"] == "MIT"
    assert about["homepage"] == "https://github.com/example/repo"
    assert about["changelog"] == "https://github.com/example/repo/blob/main/CHANGELOG.md"
    assert "javascript:alert(1)" not in json.dumps(about)


def test_about_survives_missing_metadata(monkeypatch):
    def missing(name):
        raise help_docs.importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(help_docs.importlib_metadata, "metadata", missing)
    about = help_docs.about_info()
    assert about["version"] and about["license"] is None and about["homepage"] is None


def test_pyproject_ships_markdown_docs_and_github_urls():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"docs/*.md"' in text
    assert "[project.urls]" in text and "Changelog" in text


# ---- HTTP -----------------------------------------------------------------


def _get(server, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.actual_port, timeout=3.0)
    try:
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{server.actual_port}"})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_api_help_lists_documents_and_about():
    with DesktopServer() as server:
        status, body = _get(server, "/api/help")
        assert status == 200
        data = json.loads(body)
        assert sorted(_slugs(data)) == sorted(p.stem.lower() for p in PACKAGED_DOCS.glob("*.md"))
        assert data["about"]["version"]
        for doc in data["sections"][0]["docs"]:
            assert set(doc) >= {"slug", "title"}
            assert "content" not in doc


def test_api_help_serves_known_slug_and_picks_up_new_files(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha\n\nhello\n", encoding="utf-8")
    with DesktopServer() as server:
        status, body = _get(server, "/api/help/alpha")
        assert status == 200
        doc = json.loads(body)
        assert doc["slug"] == "alpha" and doc["title"] == "Alpha" and "hello" in doc["content"]

        assert _get(server, "/api/help/gamma")[0] == 404
        (docs_root / "gamma.md").write_text("# Gamma\n", encoding="utf-8")
        assert _get(server, "/api/help/gamma")[0] == 200
        assert "gamma" in _slugs(json.loads(_get(server, "/api/help")[1]))


def test_api_help_refuses_unknown_and_traversal_slugs(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (docs_root.parent / "secret.md").write_text("# Secret\n", encoding="utf-8")
    with DesktopServer() as server:
        for path in (
            "/api/help/missing",
            "/api/help/..%2Fsecret",
            "/api/help/%2e%2e%2fsecret",
            "/api/help/..%5Csecret",
            "/api/help/../secret",
            "/api/help/alpha.md",
            "/api/help/alpha%2F..%2Fsecret",
        ):
            status, body = _get(server, path)
            assert status == 404, path
            assert b"Secret" not in body, path


def test_api_help_is_get_only_and_keeps_the_host_check(docs_root):
    (docs_root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    with DesktopServer() as server:
        port = server.actual_port
        for path in ("/api/help", "/api/help/alpha"):
            status, body = _get(server, path, host=f"evil.example:{port}")
            assert status == 403, path
            assert json.loads(body)["status"] == "rejected"
        request = urllib.request.Request(
            f"{server.url}/api/help",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "X-Desktop-Token": server.mutation_token},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3.0)
        assert error.value.code == 404


# ---- CLI ------------------------------------------------------------------


def _cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = handle_subcommand(build_parser().parse_args(list(argv)))
    return code, out.getvalue()


def test_cli_help_docs_lists_and_reads(docs_root):
    (docs_root / "alpha.md").write_text('<!-- help: section="Start" order=1 -->\n# Alpha\n\nhello\n', encoding="utf-8")

    code, out = _cli("help-docs", "--json")
    assert code == 0
    assert _slugs(json.loads(out)) == ["alpha"]

    code, out = _cli("help-docs")
    assert code == 0 and "alpha" in out and "Start" in out

    code, out = _cli("help-docs", "alpha", "--json")
    assert code == 0
    assert json.loads(out)["content"].startswith("# Alpha")

    code, out = _cli("help-docs", "alpha")
    assert code == 0 and "hello" in out


def test_cli_help_docs_unknown_slug_fails(docs_root):
    code, out = _cli("help-docs", "../etc", "--json")
    assert code == 1
    assert json.loads(out)["status"] == "failed"


def test_cli_help_docs_has_help_text(capsys):
    with pytest.raises(SystemExit) as stop:
        build_parser().parse_args(["help-docs", "--help"])
    assert stop.value.code == 0
    assert "slug" in capsys.readouterr().out


# ---- wheel ----------------------------------------------------------------


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_wheel_assets", ROOT / "dev" / "tools" / "verify_wheel_assets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_wheel(path, names):
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return path


def test_wheel_check_requires_every_packaged_document(tmp_path):
    verifier = _load_verifier()
    assets = sorted(verifier.REQUIRED_ASSETS)
    docs = [f"local_coding_agent/docs/{p.name}" for p in sorted(PACKAGED_DOCS.glob("*.md"))]
    assert docs

    complete = _make_wheel(tmp_path / "ok.whl", assets + docs)
    assert verifier.verify_wheel(complete) == []

    partial = _make_wheel(tmp_path / "partial.whl", assets + docs[1:])
    assert verifier.verify_wheel(partial) == [docs[0]]
