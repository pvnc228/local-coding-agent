# Agent guide

Local Coding Agent delegates small coding tasks to local models served by Ollama or llama.cpp. Start with [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Setup

```bash
pip install -e .[mcp,dev]
python -m local_coding_agent doctor
python -m local_coding_agent test-run --mock
python -m pytest
```

Tests live in `dev/tests`, build scripts in `dev/tools`.

## Rules for changes

- **Isolated context:** a local model receives only the files its task allows.
- **Proposal only:** a local model never writes to disk. It proposes a patch and the controller verifies it.
- **External evidence:** a check counts only when a real test runner ran it. Model reports are not evidence.
- **Scope:** reject any patch that touches files outside the allowlist.
- **Apply and rollback:** record the workspace baseline before applying and restore only the controller's own changes. Never run a broad `git restore` on a shared checkout.
- **Interfaces:** every feature is available through `python -m local_coding_agent <subcommand>` with `--json`, exit codes and `--help`.
- **Skill sync:** when task contracts or subcommands change, update both `skills/local-coding-agent/SKILL.md` and `_EMBEDDED_SKILL_MD` in `local_coding_agent/skill_config.py`.
- **Cross-platform:** output and paths must work on Windows, Linux and macOS.
- **Tests first:** add a test that shows the required behaviour, then change the code.
- **Git:** do not commit, push, tag or release unless the user asks.

Maintainers may keep extra local rules in the git-ignored `.agents/` folder. Read them if they exist.
