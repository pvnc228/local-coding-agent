"""Tests for Desktop UI Theming, Settings Persistence, API and CLI Parity (Plan 07)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from local_coding_agent.cli import build_parser, handle_subcommand
from local_coding_agent.desktop.server import DesktopServer
from local_coding_agent.desktop.styles import DESKTOP_CSS
from local_coding_agent.desktop.ui import render_desktop_html


def test_ui_settings_defaults_and_corrupt_file(tmp_path: Path):
    from local_coding_agent.desktop.settings import (
        DEFAULT_SETTINGS,
        load_ui_settings,
        save_ui_settings,
    )

    settings_file = tmp_path / "ui_settings.json"

    # 1. Non-existent file returns defaults
    s = load_ui_settings(settings_file)
    assert s["scheme"] == DEFAULT_SETTINGS["scheme"]
    assert s["preset"] == "night-galley"
    assert "custom" in s

    # 2. Corrupt JSON returns defaults
    settings_file.write_text("{corrupt-json", encoding="utf-8")
    s2 = load_ui_settings(settings_file)
    assert s2["preset"] == "night-galley"

    # 3. Valid save persists
    s2["preset"] = "graphite"
    save_ui_settings(s2, settings_file)
    loaded = load_ui_settings(settings_file)
    assert loaded["preset"] == "graphite"


def test_ui_settings_validation():
    from local_coding_agent.desktop.settings import validate_ui_settings

    # 1. Valid payload
    valid = {
        "scheme": "system",
        "preset": "ocean",
        "custom": {
            "dark": {"accent": "#38bdf8"},
        },
    }
    clean, err = validate_ui_settings(valid)
    assert err is None
    assert clean["preset"] == "ocean"
    assert clean["scheme"] == "system"

    # 2. Invalid hex color
    invalid_hex = {
        "scheme": "dark",
        "preset": "custom",
        "custom": {"dark": {"accent": "red"}},  # not #rrggbb
    }
    clean2, err2 = validate_ui_settings(invalid_hex)
    assert err2 is not None
    assert "hex" in err2.lower()

    # 3. Unknown top-level key rejected
    unknown_key = {"scheme": "dark", "preset": "grove", "unknown_field": True}
    clean3, err3 = validate_ui_settings(unknown_key)
    assert err3 is not None
    assert "unknown" in err3.lower()

    # 4. Invalid preset rejected
    invalid_preset = {"scheme": "dark", "preset": "super-rainbow"}
    clean4, err4 = validate_ui_settings(invalid_preset)
    assert err4 is not None
    assert "preset" in err4.lower()


def test_api_ui_settings_get_and_post(tmp_path: Path):
    import urllib.request
    from local_coding_agent.desktop.settings import load_ui_settings

    settings_file = tmp_path / "ui_settings.json"
    with patch("local_coding_agent.desktop.settings._SETTINGS_FILE", settings_file):
        with DesktopServer(workspace=tmp_path) as server:
            token = server.mutation_token

            # 1. GET /api/ui-settings
            url = f"{server.url}/api/ui-settings"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["preset"] == "night-galley"

            # 2. POST without token -> 403
            post_req_bad = urllib.request.Request(
                url,
                data=json.dumps({"preset": "graphite"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(post_req_bad)
                pytest.fail("Expected 403 Forbidden without token")
            except urllib.error.HTTPError as e:
                assert e.code == 403

            # 3. POST with token -> updates settings
            post_req_good = urllib.request.Request(
                url,
                data=json.dumps({"preset": "graphite", "scheme": "light"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Desktop-Token": token,
                },
                method="POST",
            )
            with urllib.request.urlopen(post_req_good) as resp:
                assert resp.status == 200
                resp_data = json.loads(resp.read().decode("utf-8"))
                assert resp_data["status"] == "ok"
                assert resp_data["settings"]["preset"] == "graphite"
                assert resp_data["settings"]["scheme"] == "light"

            # Verify persisted to disk
            persisted = load_ui_settings(settings_file)
            assert persisted["preset"] == "graphite"
            assert persisted["scheme"] == "light"


def test_cli_ui_settings_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture):
    settings_file = tmp_path / "ui_settings.json"
    with patch("local_coding_agent.desktop.settings._SETTINGS_FILE", settings_file):
        parser = build_parser()

        # 1. get action
        args = parser.parse_args(["ui-settings", "get", "--json"])
        code = handle_subcommand(args)
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["preset"] == "night-galley"

        # 2. set action
        args2 = parser.parse_args(["ui-settings", "set", "--preset", "ember", "--scheme", "dark", "--json"])
        code2 = handle_subcommand(args2)
        assert code2 == 0
        captured2 = capsys.readouterr()
        data2 = json.loads(captured2.out)
        assert data2["preset"] == "ember"
        assert data2["scheme"] == "dark"


def test_all_presets_satisfy_contrast_thresholds():
    from local_coding_agent.desktop.settings import PRESET_TOKENS, calculate_contrast_ratio

    for name, schemes in PRESET_TOKENS.items():
        for scheme_name in ("dark", "light"):
            tokens = schemes[scheme_name]
            ground = tokens["ground"]
            text = tokens["text"]

            # Text to ground contrast must be >= 4.5:1
            ratio = calculate_contrast_ratio(text, ground)
            assert ratio >= 4.5, f"Preset '{name}' ({scheme_name}) text contrast {ratio:.2f} < 4.5:1"

            # Diff additions and deletions contrast against diff background must be >= 3.0:1
            diff_bg = tokens.get("diff_bg", ground)
            diff_add = tokens.get("diff_add", tokens["blue"])
            diff_del = tokens.get("diff_del", tokens["red"])

            ratio_add = calculate_contrast_ratio(diff_add, diff_bg)
            assert ratio_add >= 3.0, f"Preset '{name}' ({scheme_name}) diff_add contrast {ratio_add:.2f} < 3.0:1"
            ratio_del = calculate_contrast_ratio(diff_del, diff_bg)
            assert ratio_del >= 3.0, f"Preset '{name}' ({scheme_name}) diff_del contrast {ratio_del:.2f} < 3.0:1"


def test_desktop_html_injects_ui_settings():
    html = render_desktop_html()
    assert "INJECTED_UI_SETTINGS" in html
    assert "data-preset" in html
    assert "dialog--wide" in html
