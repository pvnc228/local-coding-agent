from pathlib import Path

from local_coding_agent.skill_config import _EMBEDDED_SKILL_MD, get_skill_content


def test_embedded_skill_matches_disk() -> None:
    """AGENTS.md invariant: SKILL.md and _EMBEDDED_SKILL_MD must never drift."""
    disk = (Path(__file__).resolve().parent.parent / "skills" / "local-coding-agent" / "SKILL.md").read_text(encoding="utf-8")
    assert _EMBEDDED_SKILL_MD == disk
    assert get_skill_content() == disk
