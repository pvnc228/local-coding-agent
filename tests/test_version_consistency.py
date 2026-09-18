"""Release metadata must describe one package version everywhere."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RELEASE_VERSION = "1.0.4"


def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8-sig")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    assert match, "pyproject.toml must define a project version"
    return match.group(1)


def test_all_release_metadata_matches_pyproject_version():
    expected = _project_version()
    python_init = (ROOT / "local_coding_agent" / "__init__.py").read_text(encoding="utf-8-sig")
    mcp_server = (ROOT / "local_coding_agent" / "mcp_server.py").read_text(encoding="utf-8-sig")
    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8-sig")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8-sig"))
    package_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8-sig"))
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8-sig"))
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8-sig")
    cargo_lock = (ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8-sig")

    assert re.search(rf'__version__\s*=\s*"{re.escape(expected)}"', python_init)
    assert re.search(rf'_SERVER_VERSION\s*=\s*"{re.escape(expected)}"', mcp_server)
    assert f"version-{expected}-blue.svg" in readme
    assert f"Current public release: v{PUBLIC_RELEASE_VERSION}" in readme
    assert f"releases/download/v{PUBLIC_RELEASE_VERSION}/local_coding_agent-{PUBLIC_RELEASE_VERSION}-py3-none-any.whl" in readme
    assert f"releases/download/v{PUBLIC_RELEASE_VERSION}/local_coding_agent-{PUBLIC_RELEASE_VERSION}.tar.gz" in readme
    assert f"releases/download/v{PUBLIC_RELEASE_VERSION}/Local.AI.Coding.Harness_{PUBLIC_RELEASE_VERSION}_x64-setup.exe" in readme
    assert f"releases/download/v{PUBLIC_RELEASE_VERSION}/SHA256SUMS.txt" in readme
    assert f"Upcoming {expected} source release" not in readme
    assert "has not been tagged or published yet" not in readme
    assert re.search(rf"^## \[{re.escape(expected)}\](?:\s|$)", changelog, re.MULTILINE)
    assert package["version"] == expected
    assert package_lock["version"] == expected
    assert package_lock["packages"][""]["version"] == expected
    assert tauri["version"] == expected
    assert re.search(rf'^version\s*=\s*"{re.escape(expected)}"\s*$', cargo, re.MULTILINE)
    assert re.search(
        rf'name = "local-coding-agent-desktop"\s+version = "{re.escape(expected)}"',
        cargo_lock,
    )


def test_ci_and_release_workflows_gate_and_publish_the_windows_installer():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8-sig")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8-sig")

    assert "windows-desktop-tauri" in ci
    assert "npm ci" in ci
    assert "npm run tauri -- build --bundles nsis" in ci
    action_pattern = re.compile(r"^\s*-?\s*uses:\s+[^\s#]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)
    mutable_action_pattern = re.compile(r"^\s*-?\s*uses:\s+[^\s#]+@(?![0-9a-f]{40}\b)[^\s#]+", re.MULTILINE)
    assert not mutable_action_pattern.search(ci)
    assert not mutable_action_pattern.search(release)
    assert len(action_pattern.findall(ci)) >= 7
    assert len(action_pattern.findall(release)) >= 14
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in ci
    assert "desktop-tauri" in release
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in release
    assert "tag-version" in release
    assert "permissions:\n  contents: read" in release
    assert re.search(r"^permissions:\n  contents: read\n\njobs:", release, re.MULTILINE)
    assert re.search(r"^permissions:\n  contents: read\n\njobs:", ci, re.MULTILINE)

    def job_block(name: str) -> str:
        match = re.search(
            rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            release,
            re.MULTILINE | re.DOTALL,
        )
        assert match, f"missing workflow job {name}"
        return match.group("body")

    python_build_job = job_block("build-python-artifacts")
    publish_job = job_block("publish")
    assert "    permissions:\n      contents: read" in python_build_job
    assert "persist-credentials: false" in python_build_job
    assert "python -m build" in python_build_job
    assert "pip install build twine" in python_build_job
    assert "python-release-assets" in python_build_job
    assert "    permissions:\n      contents: write\n      actions: read" in publish_job
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in publish_job
    assert "actions/checkout@" not in publish_job
    for forbidden in ("pip install", "python -m build", "npm ", "cargo ", "twine ", "verify_wheel_assets.py", "extract_release_notes.py"):
        assert forbidden not in publish_job, f"publish job must not run {forbidden!r}"

    assert "git fetch --force --tags origin" in release
    assert 'git cat-file -t "refs/tags/$TAG"' in release
    assert 'git rev-parse "refs/tags/$TAG^{}"' in release
    assert 'git checkout --detach "refs/tags/$TAG"' in release
    assert "needs.tag-version.outputs.commit" in release
    assert "nsis" in release
    assert "Stage flat release assets" in publish_job
    assert "release-staging/*.exe" in publish_job
    assert "release-staging/*.whl" in publish_job
    assert "release-staging/*.tar.gz" in publish_job
    assert "release-staging/SHA256SUMS.txt" in publish_job
    assert "body_path: release-staging/RELEASE_NOTES.md" in publish_job
    assert "cd release-staging" in publish_job
    assert "find . -maxdepth 1 -type f -name '*.exe' -printf '%f\\0'" in publish_job
    assert "sort -z" in release
    assert "xargs -0 sha256sum" in release
    assert "sha256sum --check SHA256SUMS.txt" in publish_job
    assert "dist/installer/*.exe" not in publish_job
    assert "dist/SHA256SUMS.txt" not in publish_job


def test_release_checksum_survives_flat_download_layout(tmp_path: Path):
    version = _project_version()
    names = (
        f"local_coding_agent-{version}-py3-none-any.whl",
        f"local_coding_agent-{version}.tar.gz",
        f"Local AI Coding Harness_{version}_x64-setup.exe",
    )
    staging = tmp_path / "release-staging"
    flat_download = tmp_path / "flat-download"
    staging.mkdir()
    flat_download.mkdir()

    for index, name in enumerate(names, start=1):
        (staging / name).write_bytes(f"artifact-{index}".encode("ascii"))

    manifest = "".join(
        f"{hashlib.sha256((staging / name).read_bytes()).hexdigest()}  {name}\n"
        for name in names
    )
    (staging / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8")

    for name in (*names, "SHA256SUMS.txt"):
        shutil.copy2(staging / name, flat_download / name)

    for line in (flat_download / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected_hash, published_name = line.split("  ", 1)
        assert Path(published_name).name == published_name
        assert hashlib.sha256((flat_download / published_name).read_bytes()).hexdigest() == expected_hash
