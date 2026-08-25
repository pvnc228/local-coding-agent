"""Unit tests for the Model Scanner and Local Model Registry."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from local_coding_agent.cli import build_parser, handle_subcommand
from local_coding_agent.model_scanner import (
    DiscoveredModel,
    LocalModelRegistry,
    ModelRegistryData,
    discover_all_gguf_models,
    get_model_registry,
)


def test_discovered_model_serialization():
    m = DiscoveredModel(
        name="qwen.gguf",
        display_name="qwen",
        path="/models/qwen.gguf",
        size_gb=4.5,
        backend="gguf",
        source="custom",
        modified_at=123456.0,
    )
    d = m.to_dict()
    assert d["name"] == "qwen.gguf"
    assert d["size_gb"] == 4.5
    assert d["backend"] == "gguf"

    restored = DiscoveredModel.from_dict(d)
    assert restored.name == m.name
    assert restored.path == m.path
    assert restored.size_gb == m.size_gb


def test_local_model_registry_custom_dirs(tmp_path: Path):
    reg_file = tmp_path / "models.json"
    registry = LocalModelRegistry(registry_file=reg_file)

    custom_dir = tmp_path / "my_models"
    custom_dir.mkdir()

    # Add custom directory
    added = registry.add_custom_directory(custom_dir)
    assert added is True
    assert str(custom_dir.resolve()) in registry.list_custom_directories()

    # Adding again should return False
    assert registry.add_custom_directory(custom_dir) is False

    # Remove custom directory
    removed = registry.remove_custom_directory(custom_dir)
    assert removed is True
    assert str(custom_dir.resolve()) not in registry.list_custom_directories()


def test_model_registry_scan_filtering(tmp_path: Path):
    reg_file = tmp_path / "models.json"
    registry = LocalModelRegistry(registry_file=reg_file)

    models_dir = tmp_path / "models"
    models_dir.mkdir()

    # 1. Valid GGUF model
    valid_gguf = models_dir / "Qwen3.5-9B.gguf"
    valid_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    # 2. Vision projector (should be skipped)
    mmproj_gguf = models_dir / "mmproj-model-f16.gguf"
    mmproj_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    # 3. Diffusion checkpoint (should be skipped)
    flux_gguf = models_dir / "flux1-dev-Q4_0.gguf"
    flux_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    # 4. Non-GGUF file (should be skipped)
    txt_file = models_dir / "readme.txt"
    txt_file.write_text("hello")

    registry.add_custom_directory(models_dir)
    discovered = registry.scan(deep=False)

    names = [m.name for m in discovered]
    assert "Qwen3.5-9B.gguf" in names
    assert "mmproj-model-f16.gguf" not in names
    assert "flux1-dev-Q4_0.gguf" not in names


def test_cli_scan_models_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture):
    reg_file = tmp_path / "models.json"
    with patch("local_coding_agent.model_scanner._REGISTRY_FILE", reg_file):
        parser = build_parser()

        # 1. List dirs (initially empty)
        args = parser.parse_args(["scan-models", "--list-dirs"])
        code = handle_subcommand(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "custom_directories" in captured.out

        # 2. Add dir
        test_dir = str(tmp_path / "extra_models")
        args = parser.parse_args(["scan-models", "--add-dir", test_dir])
        code = handle_subcommand(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "added" in captured.out

        # 3. Remove dir
        args = parser.parse_args(["scan-models", "--remove-dir", test_dir])
        code = handle_subcommand(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "removed" in captured.out


def test_desktop_ui_modular_assembly():
    from local_coding_agent.desktop.ui import render_desktop_html

    html = render_desktop_html()
    assert "<!DOCTYPE html>" in html
    assert "Local AI Coding Harness" in html
    assert "render_header" not in html  # verify functions were executed
    assert "Interactive Chat" in html
    assert "Delegated Tasks" in html
    assert "Universal Model Scanner" in html


def test_discover_llama_server_binary(tmp_path: Path):
    from local_coding_agent.model_scanner import discover_llama_server_binary

    # 1. Custom valid path
    dummy_exe = tmp_path / "my_server.exe"
    dummy_exe.write_bytes(b"\x00")
    res = discover_llama_server_binary(str(dummy_exe))
    assert res == str(dummy_exe.resolve())

    # 2. Via environment variable
    with patch.dict("os.environ", {"LLAMA_SERVER_PATH": str(dummy_exe)}):
        res_env = discover_llama_server_binary()
        assert res_env == str(dummy_exe.resolve())

    # 3. Via mock system drive relative path
    fake_drive = tmp_path / "fake_drive"
    fake_bin = fake_drive / "AI" / "llama-server" / ("llama-server.exe" if Path(dummy_exe).suffix else "llama-server")
    fake_bin.parent.mkdir(parents=True, exist_ok=True)
    fake_bin.write_bytes(b"\x00")

    with patch("local_coding_agent.model_scanner.get_live_system_path", return_value=""):
        with patch.object(LocalModelRegistry, "get_system_drives", return_value=[fake_drive]):
            with patch.dict("os.environ", {}, clear=True):
                res_drive = discover_llama_server_binary()
                assert res_drive == str(fake_bin.resolve())
