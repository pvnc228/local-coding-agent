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


def test_cli_ui_settings_diff_wrap_flag(tmp_path: Path, capsys: pytest.CaptureFixture):
    settings_file = tmp_path / "ui_settings.json"
    with patch("local_coding_agent.desktop.settings._SETTINGS_FILE", settings_file):
        code = handle_subcommand(build_parser().parse_args(["ui-settings", "set", "--diff-wrap", "--json"]))
        assert code == 0
        assert json.loads(capsys.readouterr().out)["diff_wrap"] is True


def test_diff_wrap_is_a_saved_boolean_setting(tmp_path: Path):
    from local_coding_agent.desktop.settings import load_ui_settings, update_ui_settings, validate_ui_settings

    clean, err = validate_ui_settings({"diff_wrap": True})
    assert err is None and clean == {"diff_wrap": True}
    assert validate_ui_settings({"diff_wrap": "yes"})[1] is not None

    settings_file = tmp_path / "ui_settings.json"
    update_ui_settings({"diff_wrap": True}, file_path=settings_file)
    assert load_ui_settings(settings_file)["diff_wrap"] is True


def test_preset_swatches_use_preset_tokens():
    from local_coding_agent.desktop.components import render_modals
    from local_coding_agent.desktop.settings import PRESET_TOKENS

    html = render_modals()
    for preset in ("night-galley", "graphite", "ocean", "grove", "ember"):
        assert f"background: {PRESET_TOKENS[preset]['dark']['ground']};" in html
        assert f"background: {PRESET_TOKENS[preset]['dark']['blue']};" in html
    assert "#10b981" not in html


def _css_tokens(preset: str, scheme: str) -> dict[str, str]:
    """Tokens a preset sends to the browser, read from DESKTOP_CSS with var() references resolved."""
    import re

    def block(selector: str) -> dict[str, str]:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", DESKTOP_CSS)
        assert match, f"missing CSS block {selector}"
        return {k: v.strip() for k, v in re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1))}

    tokens = block(":root.dark" if scheme == "dark" else ":root:not(.dark)")
    if preset != "night-galley":
        tokens.update(block(f':root[data-preset="{preset}"]' + (".dark" if scheme == "dark" else ":not(.dark)")))
    resolved = {}
    for name, value in tokens.items():
        while (ref := re.fullmatch(r"var\(--([\w-]+)\)", value)) is not None:
            value = tokens[ref.group(1)]
        resolved[name] = value.lower()
    return resolved


_PRESETS = ("night-galley", "graphite", "ocean", "grove", "ember")
_SCHEMES = ("dark", "light")


@pytest.mark.parametrize("preset", _PRESETS)
@pytest.mark.parametrize("scheme", _SCHEMES)
def test_preset_css_comes_from_preset_tokens(preset: str, scheme: str):
    from local_coding_agent.desktop.settings import PRESET_TOKENS

    css = _css_tokens(preset, scheme)
    for key, value in PRESET_TOKENS[preset][scheme].items():
        assert css[key.replace("_", "-")] == value, f"{preset}/{scheme} --{key}"


@pytest.mark.parametrize("preset", _PRESETS)
@pytest.mark.parametrize("scheme", _SCHEMES)
def test_preset_css_meets_contrast_thresholds(preset: str, scheme: str):
    """Text and marks are both read as 12-14px text, so both need 4.5:1; diff bands and signs need 3:1."""
    from local_coding_agent.desktop.settings import calculate_contrast_ratio

    css = _css_tokens(preset, scheme)
    misses = []
    for token in ("text", "text-2", "text-3", "red", "blue", "amber", "pass"):
        ratio = calculate_contrast_ratio(css[token], css["ground"])
        if ratio < 4.5:
            misses.append(f"--{token} {ratio:.2f}")
    ratio = calculate_contrast_ratio(css["pass-ink"], css["pass"])
    if ratio < 4.5:
        misses.append(f"--pass-ink on --pass {ratio:.2f}")
    for token in ("diff-add", "diff-del"):
        ratio = calculate_contrast_ratio(css[token], css["diff-bg"])
        if ratio < 3.0:
            misses.append(f"--{token} on --diff-bg {ratio:.2f}")
    assert misses == [], f"{preset}/{scheme}: {misses}"


@pytest.mark.parametrize("preset", _PRESETS)
@pytest.mark.parametrize("scheme", _SCHEMES)
def test_presets_keep_green_for_runner_evidence(preset: str, scheme: str):
    css = _css_tokens(preset, scheme)
    assert css["blue"] != css["pass"]
    assert (css["diff-add"] == css["pass"]) == (preset == "graphite")


def test_night_galley_token_values_unchanged():
    assert _css_tokens("night-galley", "dark")["blue"] == "#8dbcdc"
    assert _css_tokens("night-galley", "light")["pass"] == "#3a7829"
    assert "--red-bg: rgba(236, 106, 83, 0.11);" in DESKTOP_CSS
    assert "--shadow: 0 18px 40px -14px rgba(40, 34, 20, 0.28), 0 2px 8px rgba(40, 34, 20, 0.08);" in DESKTOP_CSS


def test_desktop_html_injects_ui_settings():
    html = render_desktop_html()
    assert "INJECTED_UI_SETTINGS" in html
    assert "data-preset" in html
    assert "dialog--wide" in html


def test_validate_returns_only_supplied_keys_and_never_mutates_defaults():
    import copy

    from local_coding_agent.desktop.settings import DEFAULT_SETTINGS, validate_ui_settings

    before = copy.deepcopy(DEFAULT_SETTINGS)
    clean, err = validate_ui_settings({"preset": "ember"})
    assert err is None
    assert clean == {"preset": "ember"}

    clean2, err2 = validate_ui_settings({"custom": {"dark": {"accent": "#112233"}}})
    assert err2 is None
    assert clean2 == {"custom": {"dark": {"accent": "#112233"}}}
    assert DEFAULT_SETTINGS == before


def test_partial_update_merges_into_current_settings(tmp_path: Path):
    from local_coding_agent.desktop.settings import load_ui_settings, update_ui_settings

    settings_file = tmp_path / "ui_settings.json"
    _, err = update_ui_settings({"scheme": "light", "custom": {"dark": {"accent": "#58a6ff"}}}, settings_file)
    assert err is None
    merged, err2 = update_ui_settings({"preset": "ember"}, settings_file)
    assert err2 is None
    assert merged["scheme"] == "light"
    assert merged["preset"] == "ember"
    assert merged["custom"]["dark"]["accent"] == "#58a6ff"
    assert load_ui_settings(settings_file)["scheme"] == "light"


def test_cli_custom_dark_must_be_json_object(tmp_path: Path, capsys: pytest.CaptureFixture):
    settings_file = tmp_path / "ui_settings.json"
    with patch("local_coding_agent.desktop.settings._SETTINGS_FILE", settings_file):
        parser = build_parser()
        args = parser.parse_args(["ui-settings", "set", "--custom-dark", "[1]", "--json"])
        assert handle_subcommand(args) == 2
    assert not settings_file.exists()


def test_default_palettes_meet_contrast_thresholds():
    from local_coding_agent.desktop.settings import DEFAULT_SETTINGS, contrast_failures

    for variant in ("dark", "light"):
        assert contrast_failures(variant, DEFAULT_SETTINGS["custom"][variant]) == []


def test_update_rejects_custom_color_equal_to_diff_background(tmp_path: Path):
    from local_coding_agent.desktop.settings import DEFAULT_SETTINGS, load_ui_settings, update_ui_settings

    settings_file = tmp_path / "ui_settings.json"
    merged, err = update_ui_settings({"custom": {"dark": {"diff_added": "#07090d"}}}, settings_file)
    assert merged is None
    assert "diff_added" in err
    assert load_ui_settings(settings_file)["custom"]["dark"]["diff_added"] == DEFAULT_SETTINGS["custom"]["dark"]["diff_added"]


def test_contrast_fix_produces_a_palette_that_passes(tmp_path: Path):
    from local_coding_agent.desktop.settings import contrast_failures, suggest_contrast_fix

    failing = {"surface_tint": "#f0f0f0", "accent": "#f5f5f5", "diff_added": "#07090d", "diff_removed": "#f85149"}
    assert contrast_failures("dark", failing)
    fixed = suggest_contrast_fix("dark", failing)
    assert fixed is not None
    assert contrast_failures("dark", fixed) == []


def test_api_rejects_failing_contrast_and_offers_fix(tmp_path: Path):
    import urllib.error
    import urllib.request

    settings_file = tmp_path / "ui_settings.json"
    with patch("local_coding_agent.desktop.settings._SETTINGS_FILE", settings_file):
        with DesktopServer(workspace=tmp_path) as server:
            headers = {"Content-Type": "application/json", "X-Desktop-Token": server.mutation_token}
            bad = urllib.request.Request(
                f"{server.url}/api/ui-settings",
                data=json.dumps({"custom": {"dark": {"diff_added": "#07090d"}}}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(bad)
            assert excinfo.value.code == 400
            assert "diff_added" in json.loads(excinfo.value.read().decode("utf-8"))["error"]

            fix = urllib.request.Request(
                f"{server.url}/api/ui-settings/contrast-fix",
                data=json.dumps({"variant": "dark", "custom": {"diff_added": "#07090d"}}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(fix) as resp:
                assert resp.status == 200
                fixed = json.loads(resp.read().decode("utf-8"))["custom"]
            assert fixed["diff_added"] != "#07090d"
