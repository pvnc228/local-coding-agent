# Working rules

## Scope

Этот проект строит контроллер для делегирования малых coding-задач локальным моделям Ollama и llama.cpp / llama-server.

## Invariants

- **Isolated Context**: Локальная модель не получает больше контекста, чем нужно задаче (1–3 файла, строгий allowlist).
- **External Evidence**: Никакие тесты не считаются выполненными без внешнего evidence (вызов реального тест-раннера). Словам и самоотчётам модели верить нельзя.
- **Strict Scope Boundaries**: Никакой diff не принимается без проверки затронутых файлов (изменения вне allowlist файлов немедленно отклоняются).
- **Proposal-Only by Default**: Локальная модель никогда не пишет напрямую на диск; формируется proposal (патч), который верифицируется контроллером.
- **Mediated Apply & Auto-Rollback**: Применение патчей обязано перепроверять тесты и автоматически откатывать изменения (`git restore`) при падении проверок.
- **Pinpointed Prescriptions**: Ошибки малых моделей транслируются в лаконичные детерминированные подсказки, а не в сырые трейсбеки.
- **CLI-First Parity**: Любая функциональность системы обязана быть доступна через консольный CLI (`python -m local_coding_agent <subcommand>`) со структурированным выводом (`--json`), кодами возврата (`0`/`1`) и `--help`.
- **Agent-Agnostic Interface**: Поддержка любого AI-агента как через MCP протокол (`delegate_code`, `apply_proposal`), так и через Agent Skill (`skills/local-coding-agent/SKILL.md`) или прямой терминал.
- **Skill & Config Sync**: При любых изменениях контрактов TaskEnvelope или сабкоманд обновлять как `skills/local-coding-agent/SKILL.md`, так и `_EMBEDDED_SKILL_MD` в `local_coding_agent/skill_config.py`.
- **Dogfooding on Demand**: Разработка и рефакторинг сервиса могут использовать сам сервис (`delegate` / `apply`) на локальных моделях (`Ling-3.0-tiny`, `qwen3-8b`, etc.) при явном запросе пользователя.
- **Release & Versioning Sync**: При повышении версии синхронно обновлять 5 мест: `pyproject.toml` (`version`), `local_coding_agent/__init__.py` (`__version__`), `local_coding_agent/mcp_server.py` (`_SERVER_VERSION`), `README.md` (версионный бейдж) и `CHANGELOG.md` (Keep a Changelog + Field Insights).
- **Git Tag Release Trigger**: Пайплайн GitHub Actions (`.github/workflows/release.yml`) создаёт релизы ТОЛЬКО по пушу аннотированного тега `vX.Y.Z` (`git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`). Описание релиза на GitHub при необходимости дополняется через `gh release edit vX.Y.Z --notes-file ...`.
- **Cross-Platform Resilience**: Консольный вывод и обработка путей обязаны корректно работать на Windows (`cp1252`, CRLF, `pathlib.Path.as_posix()`), Linux и macOS.
- **Active Focus — R23 Desktop AI Coding Harness**: В активной разработке находится нативный десктопный интерфейс (`local-agent desktop`) в стиле LM Studio с двойным режимом (Interactive Chat + Delegated Tasks), фоновой декомпозицией задач на планирование/исполнение и компактной телеметрией.

## Development workflow

Перед реализацией:

1. Прочитать `PROJECT.md`, `docs/ARCHITECTURE.md` и `docs/PROTOCOL.md`;
2. Проверить текущую модель через Ollama / OpenAI API (`doctor`);
3. Определить минимальный публичный seam;
4. Добавить тест, который показывает требуемое поведение (TDD);
5. Только затем менять реализацию (делегируя атомарные правки через локальный сервис при явном запросе пользователя на dogfooding).


После реализации:

1. Проверить diff;
2. Выполнить targeted checks (`pytest tests/`);
3. Проверить, что заявленные checks подтверждены внешним runner-ом;
4. Проверить кроссплатформенную совместимость (пути, кодировки, CLI parity);
5. Зафиксировать изменения и Field Insights в `CHANGELOG.md` при повышении версии;
6. Создать и запушить аннотированный тег: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.


---

## AI Agent Automated Setup Instructions

If you are an AI coding agent (Claude Code, Cursor, Windsurf, Roo Code, ChatGPT Codex, Google Antigravity, etc.) instructed to install or configure this project:

1. **Install package & dependencies**: `pip install -e .[mcp,dev]`
2. **Verify runtime environment**: `python -m local_coding_agent doctor`
3. **Register MCP server in host IDE**: `python -m local_coding_agent init-mcp --auto --write`
4. **Install Agent Skill**: `python -m local_coding_agent init-skill --write`
5. **Run end-to-end sandbox verification**: `python -m local_coding_agent test-run --mock`
6. **Run complete test suite**: `pytest tests/`



