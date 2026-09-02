"""Unit tests for Self-Healing Environment & Auto-Pulling (R21 doctor --fix)."""

import pytest
from local_coding_agent.doctor import diagnose_environment, remediate_environment, DoctorFixReport


def test_diagnose_environment_runs():
    report = diagnose_environment()
    assert report is not None
    assert len(report.checks) > 0


def test_remediate_environment_dry_run():
    fix_report = remediate_environment(write=False)
    assert isinstance(fix_report, DoctorFixReport)
    assert fix_report.success is True
    assert isinstance(fix_report.actions, list)
    assert isinstance(fix_report.recommendations, list)


def test_remediate_environment_renders_text():
    fix_report = remediate_environment(write=False)
    text = fix_report.render_text()
    assert "System Remediation" in text
    assert "Recommended Model Pulls" in text


def test_remediate_environment_preserves_partial_actions_and_reports_errors(monkeypatch):
    import local_coding_agent.mcp_config as mcp_config
    import local_coding_agent.skill_config as skill_config

    monkeypatch.setattr(
        mcp_config,
        "integrate_mcp_config",
        lambda **kwargs: {
            "results": [
                {"client": "cursor", "path": "cursor.json", "written": True},
                {
                    "client": "antigravity",
                    "path": ".gemini/config/mcp_config.json",
                    "written": False,
                    "status": "failed",
                    "error": "permission denied",
                },
            ]
        },
    )
    monkeypatch.setattr(
        skill_config,
        "integrate_skill_config",
        lambda **kwargs: {
            "results": [
                {"client": "workspace", "path": "skills/SKILL.md", "written": True}
            ]
        },
    )

    report = remediate_environment(write=True)

    assert report.success is False
    assert len(report.actions) == 2
    assert report.errors == ["antigravity: permission denied"]
    assert "Failures:" in report.render_text()
