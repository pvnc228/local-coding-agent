"""UI Settings persistence, validation, and theme token presets for Desktop AI Coding Harness."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path.home() / ".local_coding_agent"
_SETTINGS_FILE = _CONFIG_DIR / "ui_settings.json"
_LOCK = threading.Lock()

ALLOWED_SCHEMES = ("system", "dark", "light")
ALLOWED_PRESETS = ("night-galley", "graphite", "ocean", "grove", "ember", "custom")
ALLOWED_CUSTOM_KEYS = ("surface_tint", "accent", "diff_added", "diff_removed")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Predefined theme preset tokens (exact hex values for contrast validation and CSS generation)
PRESET_TOKENS: dict[str, dict[str, dict[str, str]]] = {
    "night-galley": {
        "dark": {
            "ground": "#0e1117",
            "plate": "#12161e",
            "raised": "#191e28",
            "hover": "#212734",
            "rule": "#252b37",
            "rule_strong": "#353d4c",
            "text": "#e8e3d6",
            "text_2": "#b6b1a4",
            "text_3": "#8d897d",
            "red": "#ec6a53",
            "blue": "#8dbcdc",
            "amber": "#dca84d",
            "pass": "#a2d38c",
            "pass_ink": "#0f1a0b",
            "diff_bg": "#07090d",
            "diff_gutter": "#0b0e13",
            "diff_add": "#8dbcdc",
            "diff_del": "#ec6a53",
        },
        "light": {
            "ground": "#f3f0e8",
            "plate": "#ebe7dd",
            "raised": "#fbf9f4",
            "hover": "#e2ddd1",
            "rule": "#d9d3c5",
            "rule_strong": "#c2bba9",
            "text": "#1b1e25",
            "text_2": "#45474d",
            "text_3": "#66655f",
            "red": "#b0381f",
            "blue": "#2b6896",
            "amber": "#8b5f10",
            "pass": "#3a7829",
            "pass_ink": "#f6fbf2",
            "diff_bg": "#fffdf8",
            "diff_gutter": "#f6f2e9",
            "diff_add": "#2b6896",
            "diff_del": "#b0381f",
        },
    },
    "graphite": {
        "dark": {
            "ground": "#0a0a0c",
            "plate": "#121215",
            "raised": "#1a1a1f",
            "hover": "#222228",
            "rule": "#2a2a32",
            "rule_strong": "#3e3e4a",
            "text": "#ededed",
            "text_2": "#a1a1aa",
            "text_3": "#71717a",
            "red": "#ef4444",
            "blue": "#3b82f6",
            "amber": "#f59e0b",
            "pass": "#10b981",
            "pass_ink": "#022c22",
            "diff_bg": "#050507",
            "diff_gutter": "#0d0d10",
            "diff_add": "#10b981",
            "diff_del": "#ef4444",
        },
        "light": {
            "ground": "#f8fafc",
            "plate": "#f1f5f9",
            "raised": "#ffffff",
            "hover": "#e2e8f0",
            "rule": "#cbd5e1",
            "rule_strong": "#94a3b8",
            "text": "#0f172a",
            "text_2": "#475569",
            "text_3": "#64748b",
            "red": "#dc2626",
            "blue": "#2563eb",
            "amber": "#d97706",
            "pass": "#059669",
            "pass_ink": "#ecfdf5",
            "diff_bg": "#ffffff",
            "diff_gutter": "#f8fafc",
            "diff_add": "#059669",
            "diff_del": "#dc2626",
        },
    },
    "ocean": {
        "dark": {
            "ground": "#09131d",
            "plate": "#0d1b2a",
            "raised": "#132438",
            "hover": "#1b304a",
            "rule": "#223c5b",
            "rule_strong": "#33557e",
            "text": "#e0f2fe",
            "text_2": "#93c5fd",
            "text_3": "#60a5fa",
            "red": "#f87171",
            "blue": "#38bdf8",
            "amber": "#fbbf24",
            "pass": "#34d399",
            "pass_ink": "#064e3b",
            "diff_bg": "#050b12",
            "diff_gutter": "#08121d",
            "diff_add": "#38bdf8",
            "diff_del": "#f87171",
        },
        "light": {
            "ground": "#f0f9ff",
            "plate": "#e0f2fe",
            "raised": "#ffffff",
            "hover": "#bae6fd",
            "rule": "#7dd3fc",
            "rule_strong": "#38bdf8",
            "text": "#082f49",
            "text_2": "#0369a1",
            "text_3": "#0284c7",
            "red": "#dc2626",
            "blue": "#0284c7",
            "amber": "#d97706",
            "pass": "#059669",
            "pass_ink": "#f0fdf4",
            "diff_bg": "#ffffff",
            "diff_gutter": "#f0f9ff",
            "diff_add": "#0284c7",
            "diff_del": "#dc2626",
        },
    },
    "grove": {
        "dark": {
            "ground": "#0d140e",
            "plate": "#131c14",
            "raised": "#1a261c",
            "hover": "#223325",
            "rule": "#2b3e2f",
            "rule_strong": "#3d5843",
            "text": "#ecfdf5",
            "text_2": "#a7f3d0",
            "text_3": "#6ee7b7",
            "red": "#f87171",
            "blue": "#6ee7b7",
            "amber": "#fbbf24",
            "pass": "#4ade80",
            "pass_ink": "#052e16",
            "diff_bg": "#070c08",
            "diff_gutter": "#0a110b",
            "diff_add": "#4ade80",
            "diff_del": "#f87171",
        },
        "light": {
            "ground": "#f0fdf4",
            "plate": "#dcfce7",
            "raised": "#ffffff",
            "hover": "#bbf7d0",
            "rule": "#86efac",
            "rule_strong": "#4ade80",
            "text": "#052e16",
            "text_2": "#166534",
            "text_3": "#15803d",
            "red": "#dc2626",
            "blue": "#16a34a",
            "amber": "#d97706",
            "pass": "#16a34a",
            "pass_ink": "#f0fdf4",
            "diff_bg": "#ffffff",
            "diff_gutter": "#f0f9ff",
            "diff_add": "#16a34a",
            "diff_del": "#dc2626",
        },
    },
    "ember": {
        "dark": {
            "ground": "#160e0d",
            "plate": "#1f1412",
            "raised": "#2a1b18",
            "hover": "#372320",
            "rule": "#462c28",
            "rule_strong": "#613d37",
            "text": "#fff1f2",
            "text_2": "#fecdd3",
            "text_3": "#fda4af",
            "red": "#fb7185",
            "blue": "#f97316",
            "amber": "#fbbf24",
            "pass": "#4ade80",
            "pass_ink": "#1c1917",
            "diff_bg": "#0c0706",
            "diff_gutter": "#120b0a",
            "diff_add": "#f97316",
            "diff_del": "#fb7185",
        },
        "light": {
            "ground": "#fff1f2",
            "plate": "#ffe4e6",
            "raised": "#ffffff",
            "hover": "#fecdd3",
            "rule": "#fda4af",
            "rule_strong": "#fb7185",
            "text": "#4c0519",
            "text_2": "#9f1239",
            "text_3": "#be123c",
            "red": "#e11d48",
            "blue": "#ea580c",
            "amber": "#d97706",
            "pass": "#16a34a",
            "pass_ink": "#fff1f2",
            "diff_bg": "#ffffff",
            "diff_gutter": "#fff1f2",
            "diff_add": "#ea580c",
            "diff_del": "#e11d48",
        },
    },
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "scheme": "dark",
    "preset": "night-galley",
    "custom": {
        "dark": {
            "surface_tint": "#07090d",
            "accent": "#58a6ff",
            "diff_added": "#58a6ff",
            "diff_removed": "#f85149",
        },
        "light": {
            "surface_tint": "#fffdf8",
            "accent": "#2b6896",
            "diff_added": "#2b6896",
            "diff_removed": "#b0381f",
        },
    },
}


def _relative_luminance(hex_color: str) -> float:
    """Calculate WCAG 2.1 relative luminance for a 6-digit hex color."""
    clean = hex_color.lstrip("#")
    if len(clean) != 6:
        return 0.0
    r = int(clean[0:2], 16) / 255.0
    g = int(clean[2:4], 16) / 255.0
    b = int(clean[4:6], 16) / 255.0

    def adjust(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def calculate_contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex colors (returns ratio >= 1.0)."""
    l1 = _relative_luminance(hex1)
    l2 = _relative_luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def load_ui_settings(file_path: Path | None = None) -> dict[str, Any]:
    """Load persistent UI settings from disk, returning safe defaults on error."""
    path = file_path or _SETTINGS_FILE
    if not path.exists():
        return copy.deepcopy(DEFAULT_SETTINGS)

    with _LOCK:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                clean, _ = validate_ui_settings(data, sanitize=True)
                if clean is not None:
                    return merge_ui_settings(DEFAULT_SETTINGS, clean)
        except Exception:
            pass
    return copy.deepcopy(DEFAULT_SETTINGS)


def validate_ui_settings(payload: dict[str, Any], sanitize: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a settings payload against strict allowlists; return only the keys it supplied."""
    if not isinstance(payload, dict):
        return None, "Payload must be a JSON object"

    allowed_top = {"scheme", "preset", "custom"}
    unknown = set(payload.keys()) - allowed_top
    if unknown and not sanitize:
        return None, f"Unknown settings keys: {', '.join(sorted(unknown))}"

    clean: dict[str, Any] = {}

    if "scheme" in payload:
        if payload["scheme"] in ALLOWED_SCHEMES:
            clean["scheme"] = payload["scheme"]
        elif not sanitize:
            return None, f"Invalid scheme '{payload['scheme']}'; must be one of {ALLOWED_SCHEMES}"

    if "preset" in payload:
        if payload["preset"] in ALLOWED_PRESETS:
            clean["preset"] = payload["preset"]
        elif not sanitize:
            return None, f"Invalid preset '{payload['preset']}'; must be one of {ALLOWED_PRESETS}"

    if "custom" in payload:
        custom_payload = payload["custom"]
        if not isinstance(custom_payload, dict):
            if not sanitize:
                return None, "custom must be a JSON object keyed by 'dark' and 'light'"
        else:
            clean_custom: dict[str, dict[str, str]] = {}
            for variant in ("dark", "light"):
                if variant not in custom_payload:
                    continue
                colors = custom_payload[variant]
                if not isinstance(colors, dict):
                    if not sanitize:
                        return None, f"custom.{variant} must be a JSON object"
                    continue
                variant_clean: dict[str, str] = {}
                for k, v in colors.items():
                    if k not in ALLOWED_CUSTOM_KEYS:
                        if not sanitize:
                            return None, f"Unknown custom color key '{k}'"
                        continue
                    if not isinstance(v, str) or not _HEX_RE.match(v):
                        if not sanitize:
                            return None, f"Invalid hex color for '{k}': {v} (expected #rrggbb)"
                        continue
                    variant_clean[k] = v.lower()
                clean_custom[variant] = variant_clean
            if clean_custom:
                clean["custom"] = clean_custom

    return clean, None


def merge_ui_settings(current: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    """Overlay a validated partial payload on the current settings. The desktop API and the CLI both use this."""
    merged = copy.deepcopy(current)
    for key in ("scheme", "preset"):
        if key in partial:
            merged[key] = partial[key]
    for variant, colors in partial.get("custom", {}).items():
        merged.setdefault("custom", {}).setdefault(variant, {}).update(colors)
    return merged


def save_ui_settings(settings: dict[str, Any], file_path: Path | None = None) -> dict[str, Any]:
    """Atomically save validated UI settings to disk."""
    clean, err = validate_ui_settings(settings, sanitize=False)
    if err is not None:
        raise ValueError(f"Invalid UI settings: {err}")
    assert clean is not None

    path = file_path or _SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        tmp_file = path.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(path)

    return clean


CONTRAST_TEXT_MIN = 4.5
CONTRAST_MARK_MIN = 3.0
_CONTRAST_MID_LUMINANCE = 0.18  # above this a background reads as light, so colours move toward black


def _contrast_rules(variant: str, colors: dict[str, str]) -> list[tuple[str, str, float]]:
    """Each custom role with the colour it must contrast against and the minimum ratio."""
    base = PRESET_TOKENS["night-galley"][variant]
    return [
        ("surface_tint", base["text"], CONTRAST_TEXT_MIN),  # body text sits on the ground
        ("accent", colors["surface_tint"], CONTRAST_MARK_MIN),  # labels sit on the ground
        ("diff_added", base["diff_bg"], CONTRAST_MARK_MIN),  # diff bands sit on the diff background
        ("diff_removed", base["diff_bg"], CONTRAST_MARK_MIN),
    ]


def _contrast_misses(variant: str, colors: dict[str, str]) -> list[tuple[str, str, float]]:
    return [
        (role, against, minimum)
        for role, against, minimum in _contrast_rules(variant, colors)
        if calculate_contrast_ratio(colors[role], against) < minimum
    ]


def contrast_failures(variant: str, colors: dict[str, str]) -> list[str]:
    """One message per custom role that misses its WCAG threshold; an empty list means the palette passes."""
    return [
        f"{role} {colors[role]} is {calculate_contrast_ratio(colors[role], against):.2f}:1 against {against}; needs {minimum}:1"
        for role, against, minimum in _contrast_misses(variant, colors)
    ]


def _mix_steps(hex_color: str, target: int) -> list[str]:
    """Colours from hex_color toward a pure channel value (0 or 255), one step at a time."""
    start = [int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]
    steps = []
    for k in range(1, 256):
        mixed = [round(c + (target - c) * k / 255) for c in start]
        steps.append("#" + "".join(f"{c:02x}" for c in mixed))
    return steps


def suggest_contrast_fix(variant: str, colors: dict[str, str]) -> dict[str, str] | None:
    """Move failing roles away from their background until they pass; None when a role cannot pass."""
    fixed = dict(colors)
    for _ in range(len(fixed) + 1):
        misses = _contrast_misses(variant, fixed)
        if not misses:
            return fixed
        role, against, minimum = misses[0]
        target = 0 if _relative_luminance(against) > _CONTRAST_MID_LUMINANCE else 255
        candidate = next(
            (c for c in _mix_steps(fixed[role], target) if calculate_contrast_ratio(c, against) >= minimum),
            None,
        )
        if candidate is None:
            return None
        fixed[role] = candidate
    return None


def update_ui_settings(payload: dict[str, Any], file_path: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a partial update, merge it into the saved settings, enforce contrast, then save.

    The desktop API and the CLI both call this, so they accept and reject the same values.
    """
    clean, err = validate_ui_settings(payload)
    if err is not None:
        return None, err
    assert clean is not None

    merged = merge_ui_settings(load_ui_settings(file_path), clean)
    for variant in clean.get("custom", {}):
        problems = contrast_failures(variant, merged["custom"][variant])
        if problems:
            return None, f"Contrast too low ({variant}): " + "; ".join(problems)

    save_ui_settings(merged, file_path)
    return merged, None
