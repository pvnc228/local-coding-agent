"""MCP client configuration generator and integrator."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_CODEX_CLIENTS = frozenset(
    {
        "chatgpt",
        "chatgpt-desktop",
        "codex",
        "codex-desktop",
        "codex-cli",
        "openai",
    }
)


def get_client_config_path(client: str, workspace: str | Path = ".") -> Path:
    client_norm = client.lower().strip()
    home = Path.home()
    ws = Path(workspace).resolve()

    if client_norm in ("claude", "claude-desktop", "claudedesktop", "claude-code"):
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else home / "AppData" / "Roaming"
            return base / "Claude" / "claude_desktop_config.json"
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        # Linux
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else home / ".config"
        return base / "Claude" / "claude_desktop_config.json"

    if client_norm in ("cursor", "cursor-ide"):
        return ws / ".cursor" / "mcp.json"

    if client_norm in ("windsurf", "codeium"):
        return home / ".codeium" / "windsurf" / "mcp_config.json"

    if client_norm in ("cline", "cline-desktop", "roo", "roo-code", "vscode"):
        # Check workspace .vscode/mcp.json or user-level .cline/mcp.json
        if (ws / ".vscode").is_dir():
            return ws / ".vscode" / "mcp.json"
        return home / ".cline" / "mcp.json"

    if client_norm in ("antigravity", "antigravity-desktop", "agy"):
        return home / ".gemini" / "config" / "mcp_config.json"

    if client_norm in ("opencode", "opencode-desktop", "opencode-cli"):
        return home / ".config" / "opencode" / "opencode.jsonc"

    if client_norm in _CODEX_CLIENTS:
        return home / ".codex" / "config.toml"

    raise ValueError(
        f"Unsupported MCP client: {client}. Supported: claude, cursor, windsurf, cline, antigravity, opencode, codex, chatgpt, vscode"
    )


def detect_installed_clients(workspace: str | Path = ".") -> list[str]:
    """Detect available IDE / MCP clients in workspace and host environment."""
    ws = Path(workspace).resolve()
    home = Path.home()
    detected: list[str] = []

    # Check workspace directories
    if (ws / ".cursor").is_dir():
        detected.append("cursor")
    if (ws / ".vscode").is_dir():
        detected.append("cline")

    # Check user-level configurations
    try:
        claude_path = get_client_config_path("claude", ws)
        if claude_path.parent.is_dir() or claude_path.exists():
            detected.append("claude")
    except Exception:
        pass

    try:
        windsurf_path = get_client_config_path("windsurf", ws)
        if windsurf_path.parent.is_dir() or windsurf_path.exists():
            detected.append("windsurf")
    except Exception:
        pass

    try:
        if (home / ".gemini").is_dir():
            detected.append("antigravity")
    except Exception:
        pass

    try:
        if (home / ".config" / "opencode").is_dir() or (home / ".opencode").is_dir():
            detected.append("opencode")
    except Exception:
        pass

    try:
        if (home / ".codex").is_dir():
            detected.append("codex")
    except Exception:
        pass

    return detected



def generate_mcp_config_dict(
    workspace: str | Path,
    profile: str = "qwen3-8b-q6k",
    endpoint: str | None = None,
    command: str | None = None,
    server_name: str = "local-coding-agent",
    client: str = "generic",
) -> dict[str, Any]:
    cmd = command or sys.executable
    args = ["-m", "local_coding_agent", "serve-mcp", "--workspace", str(Path(workspace).resolve()), "--profile", profile]
    if endpoint:
        args.extend(["--endpoint", endpoint])

    client_norm = client.lower().strip()
    if client_norm in ("opencode", "opencode-desktop", "opencode-cli"):
        return {
            "mcp": {
                server_name: {
                    "type": "local",
                    "command": [cmd, *args],
                    "enabled": True,
                }
            }
        }

    return {
        "mcpServers": {
            server_name: {
                "command": cmd,
                "args": args,
            }
        }
    }


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _toml_table_name(server_name: str) -> str:
    return f"mcp_servers.{_toml_key(server_name)}"


def _render_codex_server_toml(server_name: str, server: dict[str, Any]) -> str:
    args = ", ".join(json.dumps(str(arg), ensure_ascii=False) for arg in server["args"])
    table = _toml_table_name(server_name)
    return (
        f"[{table}]\n"
        f"command = {json.dumps(str(server['command']), ensure_ascii=False)}\n"
        f"args = [{args}]\n"
    )


def _merge_codex_server_toml(existing: str, server_name: str, server: dict[str, Any]) -> str:
    """Replace one Codex MCP table while preserving unrelated TOML settings."""
    replacement = _render_codex_server_toml(server_name, server).rstrip()
    text = existing.rstrip()
    table = _toml_table_name(server_name)
    section_pattern = re.compile(r"(?m)^\[([^\]\r\n]+)\][ \t]*$")
    sections = list(section_pattern.finditer(text))
    target_index = next(
        (
            index
            for index, section in enumerate(sections)
            if section.group(1).strip() == table
        ),
        None,
    )

    if target_index is None:
        return f"{text}\n\n{replacement}\n" if text else f"{replacement}\n"

    start = sections[target_index].start()
    end = len(text)
    for section in sections[target_index + 1 :]:
        name = section.group(1).strip()
        if not name.startswith(f"{table}."):
            end = section.start()
            break

    prefix = text[:start].rstrip()
    suffix = text[end:].lstrip()
    parts = [part for part in (prefix, replacement, suffix) if part]
    return "\n\n".join(parts) + "\n"


def _read_json_config(path: Path) -> dict[str, Any]:
    """Read a JSON/JSONC config without treating corruption as empty data."""
    raw = path.read_text(encoding="utf-8-sig")
    cleaned_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        cleaned_lines.append(line)
    loaded = json.loads("\n".join(cleaned_lines))
    if not isinstance(loaded, dict):
        raise ValueError("MCP config root must be a JSON object")
    return loaded


def _atomic_write_text(path: Path, text: str) -> None:
    """Write a config through a same-directory temporary file and replace."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _config_read_failure(client: str, path: Path, error: Exception, dry_run: bool) -> dict[str, Any]:
    return {
        "client": client,
        "path": str(path),
        "dry_run": dry_run,
        "written": False,
        "status": "failed",
        "error": f"Cannot read existing MCP config without risking data loss: {error}",
    }


def integrate_mcp_config(
    client: str,
    workspace: str | Path = ".",
    profile: str = "qwen3-8b-q6k",
    target_path: Path | None = None,
    dry_run: bool = False,
    endpoint: str | None = None,
    server_name: str = "local-coding-agent",
) -> dict[str, Any]:
    client_norm = client.lower().strip()

    if client_norm in ("auto", "all", "*"):
        targets = detect_installed_clients(workspace)
        sub_results = []
        all_written = True
        for tgt in targets:
            try:
                sub_res = integrate_mcp_config(
                    client=tgt,
                    workspace=workspace,
                    profile=profile,
                    dry_run=dry_run,
                    endpoint=endpoint,
                    server_name=server_name,
                )
            except Exception as error:
                try:
                    failed_path = str(get_client_config_path(tgt, workspace))
                except Exception:
                    failed_path = ""
                sub_res = {
                    "client": tgt,
                    "path": failed_path,
                    "dry_run": dry_run,
                    "written": False,
                    "status": "failed",
                    "error": str(error),
                }
            sub_results.append(sub_res)
            if not sub_res.get("written", False):
                all_written = False
        return {
            "client": client,
            "detected_clients": targets,
            "results": sub_results,
            "dry_run": dry_run,
            "written": all_written if not dry_run else False,
        }

    resolved_path = target_path if target_path is not None else get_client_config_path(client, workspace)
    is_opencode = client_norm in ("opencode", "opencode-desktop", "opencode-cli") or resolved_path.name.startswith("opencode.")
    is_toml = resolved_path.suffix.lower() == ".toml"

    snippet = generate_mcp_config_dict(
        workspace=workspace,
        profile=profile,
        endpoint=endpoint,
        server_name=server_name,
        client="opencode" if is_opencode else client_norm,
    )

    if is_toml:
        server = snippet["mcpServers"][server_name]
        if resolved_path.exists():
            try:
                existing = resolved_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as error:
                return _config_read_failure(client, resolved_path, error, dry_run)
        else:
            existing = ""
        merged_text = _merge_codex_server_toml(existing, server_name, server)
        if dry_run:
            return {
                "client": client,
                "path": str(resolved_path),
                "dry_run": True,
                "written": False,
                "config": merged_text,
                "snippet": snippet,
            }

        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _atomic_write_text(resolved_path, merged_text)
        except OSError as error:
            return {
                "client": client,
                "path": str(resolved_path),
                "dry_run": False,
                "written": False,
                "status": "failed",
                "error": f"Cannot write MCP config atomically: {error}",
            }
        return {
            "client": client,
            "path": str(resolved_path),
            "dry_run": False,
            "written": True,
            "config": merged_text,
            "snippet": snippet,
        }

    merged_data: dict[str, Any] = {}

    if resolved_path.exists():
        try:
            merged_data = _read_json_config(resolved_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            return _config_read_failure(client, resolved_path, error, dry_run)

    if is_opencode:
        if "mcp" not in merged_data or not isinstance(merged_data["mcp"], dict):
            merged_data["mcp"] = {}
        # Clean up any errant mcpServers key in opencode config which violates opencode.json schema
        if "mcpServers" in merged_data:
            del merged_data["mcpServers"]
        merged_data["mcp"][server_name] = snippet["mcp"][server_name]
    else:
        if "mcpServers" not in merged_data or not isinstance(merged_data["mcpServers"], dict):
            merged_data["mcpServers"] = {}
        merged_data["mcpServers"][server_name] = snippet["mcpServers"][server_name]

    if dry_run:
        return {
            "client": client,
            "path": str(resolved_path),
            "dry_run": True,
            "written": False,
            "config": merged_data,
            "snippet": snippet,
        }

    # Ensure parent directory exists
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write_text(resolved_path, json.dumps(merged_data, indent=2, ensure_ascii=False))
    except OSError as error:
        return {
            "client": client,
            "path": str(resolved_path),
            "dry_run": False,
            "written": False,
            "status": "failed",
            "error": f"Cannot write MCP config atomically: {error}",
        }

    return {
        "client": client,
        "path": str(resolved_path),
        "dry_run": False,
        "written": True,
        "config": merged_data,
        "snippet": snippet,
    }
