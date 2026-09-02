"""Tests for MCP config generator and integrator."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_coding_agent.mcp_config import (
    generate_mcp_config_dict,
    get_client_config_path,
    integrate_mcp_config,
)


class TestMcpConfig(unittest.TestCase):
    def test_generate_mcp_config_dict_defaults(self):
        conf = generate_mcp_config_dict(
            workspace="c:/workspace",
            profile="qwen3-8b-q6k",
        )
        self.assertIn("mcpServers", conf)
        self.assertIn("local-coding-agent", conf["mcpServers"])
        server = conf["mcpServers"]["local-coding-agent"]
        self.assertIn("command", server)
        self.assertIn("args", server)
        self.assertIn("serve-mcp", server["args"])
        self.assertIn("--profile", server["args"])
        self.assertIn("qwen3-8b-q6k", server["args"])

    def test_get_client_config_path_claude(self):
        path = get_client_config_path("claude")
        self.assertIsInstance(path, Path)
        self.assertTrue(path.name.endswith("claude_desktop_config.json"))

    def test_get_client_config_path_cursor(self):
        path = get_client_config_path("cursor")
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith("mcp.json"))

    def test_get_client_config_path_antigravity(self):
        path = get_client_config_path("antigravity")
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith("mcp_config.json"))

    def test_get_client_config_path_opencode(self):
        path = get_client_config_path("opencode")
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith("opencode.jsonc") or str(path).endswith("mcp.json"))

    def test_get_client_config_path_cline(self):
        path = get_client_config_path("cline")
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith("mcp.json"))

    def test_get_client_config_path_chatgpt(self):
        path = get_client_config_path("chatgpt")
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith("config.toml"))

    def test_get_client_config_path_codex_is_toml(self):
        path = get_client_config_path("codex")
        self.assertEqual(path, Path.home() / ".codex" / "config.toml")


    def test_integrate_mcp_config_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.json"
            res = integrate_mcp_config(
                client="claude",
                workspace=tmpdir,
                profile="qwen2.5-coder",
                target_path=cfg_file,
                dry_run=True,
            )
            self.assertFalse(cfg_file.exists())
            self.assertTrue(res["dry_run"])
            self.assertIn("mcpServers", res["config"])

    def test_integrate_mcp_config_write_and_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.json"
            # Create existing config with other server
            initial_data = {
                "mcpServers": {
                    "existing-server": {
                        "command": "node",
                        "args": ["server.js"]
                    }
                }
            }
            cfg_file.write_text(json.dumps(initial_data), encoding="utf-8")

            res = integrate_mcp_config(
                client="claude",
                workspace=tmpdir,
                profile="qwen3-8b-q6k",
                target_path=cfg_file,
                dry_run=False,
            )
            self.assertTrue(cfg_file.exists())
            self.assertFalse(res["dry_run"])
            self.assertTrue(res["written"])

            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertIn("existing-server", saved["mcpServers"])
            self.assertIn("local-coding-agent", saved["mcpServers"])

    def test_integrate_codex_config_writes_idempotent_toml_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.toml"
            cfg_file.write_text(
                "[mcp_servers.other]\ncommand = 'node'\n\n"
                "[mcp_servers.local-coding-agent]\ncommand = 'old-python'\nargs = []\n",
                encoding="utf-8",
            )

            first = integrate_mcp_config(
                client="codex",
                workspace=tmpdir,
                profile="qwen3-8b-q6k",
                target_path=cfg_file,
                dry_run=False,
            )
            second = integrate_mcp_config(
                client="codex",
                workspace=tmpdir,
                profile="qwen3-8b-q6k",
                target_path=cfg_file,
                dry_run=False,
            )
            saved = cfg_file.read_text(encoding="utf-8")

            self.assertTrue(first["written"])
            self.assertTrue(second["written"])
            self.assertIn("[mcp_servers.other]", saved)
            self.assertIn("[mcp_servers.local-coding-agent]", saved)
            self.assertNotIn("old-python", saved)
            self.assertEqual(saved.count("[mcp_servers.local-coding-agent]"), 1)
            self.assertIn("serve-mcp", saved)
            self.assertNotIn('"mcpServers"', saved)

    def test_auto_detects_codex_client_for_toml_config(self):
        from local_coding_agent.mcp_config import detect_installed_clients

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            (home / ".codex").mkdir()
            with mock.patch("local_coding_agent.mcp_config.Path.home", return_value=home):
                self.assertIn("codex", detect_installed_clients(workspace=home))

    def test_auto_detect_clients_with_workspace_cursor_and_vscode(self):
        from local_coding_agent.mcp_config import detect_installed_clients

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / ".cursor").mkdir()
            (ws / ".vscode").mkdir()

            detected = detect_installed_clients(workspace=ws)
            self.assertIn("cursor", detected)
            self.assertIn("cline", detected)


    @mock.patch("local_coding_agent.mcp_config.detect_installed_clients", return_value=["cursor"])
    def test_integrate_auto_clients_writes_to_detected(self, mock_detect):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            cursor_dir = ws / ".cursor"
            cursor_dir.mkdir()

            res = integrate_mcp_config(
                client="auto",
                workspace=ws,
                profile="qwen3-8b-q6k",
                dry_run=False,
            )
            self.assertTrue((cursor_dir / "mcp.json").exists())
            saved = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))
            self.assertIn("local-coding-agent", saved["mcpServers"])

    @mock.patch(
        "local_coding_agent.mcp_config.detect_installed_clients",
        return_value=["cursor", "antigravity"],
    )
    def test_integrate_auto_clients_isolates_unwritable_target(self, mock_detect):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cursor_path = root / "cursor" / "mcp.json"
            denied_path = root / ".gemini" / "config" / "mcp_config.json"
            original_write_text = Path.write_text

            def guarded_write_text(path, *args, **kwargs):
                if path == denied_path:
                    raise PermissionError("permission denied")
                return original_write_text(path, *args, **kwargs)

            def config_path(client, workspace="."):
                return cursor_path if client == "cursor" else denied_path

            with mock.patch(
                "local_coding_agent.mcp_config.get_client_config_path",
                side_effect=config_path,
            ), mock.patch.object(Path, "write_text", guarded_write_text):
                result = integrate_mcp_config(
                    client="auto",
                    workspace=root,
                    dry_run=False,
                )

            by_client = {item["client"]: item for item in result["results"]}
            self.assertTrue(by_client["cursor"]["written"])
            self.assertFalse(by_client["antigravity"]["written"])
            self.assertEqual(by_client["antigravity"]["status"], "failed")
            self.assertIn("permission denied", by_client["antigravity"]["error"])
            self.assertTrue(cursor_path.exists())


    def test_generate_mcp_config_dict_opencode(self):
        conf = generate_mcp_config_dict(
            workspace="c:/workspace",
            profile="qwen3-8b-q6k",
            client="opencode",
        )
        self.assertIn("mcp", conf)
        self.assertNotIn("mcpServers", conf)
        self.assertIn("local-coding-agent", conf["mcp"])
        server = conf["mcp"]["local-coding-agent"]
        self.assertEqual(server["type"], "local")
        self.assertIsInstance(server["command"], list)
        self.assertIn("serve-mcp", server["command"])
        self.assertIn("qwen3-8b-q6k", server["command"])
        self.assertTrue(server.get("enabled", True))

    def test_integrate_mcp_config_opencode_merges_mcp_and_cleans_mcpservers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "opencode.jsonc"
            initial_data = {
                "$schema": "https://opencode.ai/config.json",
                "plugin": ["@dietrichgebert/ponytail"],
                "mcpServers": {
                    "local-coding-agent": {
                        "command": "python",
                        "args": ["-m", "local_coding_agent"]
                    }
                }
            }
            cfg_file.write_text(json.dumps(initial_data), encoding="utf-8")

            res = integrate_mcp_config(
                client="opencode",
                workspace=tmpdir,
                profile="qwen3-8b-q6k",
                target_path=cfg_file,
                dry_run=False,
            )
            self.assertTrue(cfg_file.exists())
            self.assertTrue(res["written"])

            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertIn("mcp", saved)
            self.assertNotIn("mcpServers", saved)
            self.assertIn("local-coding-agent", saved["mcp"])
            self.assertEqual(saved["mcp"]["local-coding-agent"]["type"], "local")
            self.assertIsInstance(saved["mcp"]["local-coding-agent"]["command"], list)
            self.assertIn("serve-mcp", saved["mcp"]["local-coding-agent"]["command"])


if __name__ == "__main__":
    unittest.main()
