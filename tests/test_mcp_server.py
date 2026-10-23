import asyncio
import json
import sys
import tempfile
import time
from threading import Event
import unittest
from pathlib import Path

from local_coding_agent.mcp_server import build_server
from local_coding_agent.service import DelegationService


class FakeModel:
    def chat(self, messages, *, tools=None):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "status": "candidate",
                        "summary": "готово",
                        "patch": "",
                        "checks": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
            }
        }


class BlockingService:
    def __init__(self):
        self.started = Event()

    def delegate(self, caller_id, request):
        self.started.set()
        time.sleep(0.25)
        return {
            "status": "accepted",
            "summary": "ok",
            "patch": "",
            "checks": [],
            "risks": [],
        }


class McpSdkServerTests(unittest.TestCase):
    def _service(self, workspace: Path) -> DelegationService:
        return DelegationService({"fixture": workspace}, model_factory=lambda profile: FakeModel())

    def _arguments(self):
        return {
            "request_id": "mcp-request-1",
            "workspace_ref": "fixture",
            "model_profile": "qwen2.5-1.5b",
            "task": {"id": "mcp-task-1", "goal": "прочитать файл", "files": ["allowed.py"]},
        }

    def test_delegate_code_via_in_process_client(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            server = build_server(self._service(workspace))

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    tools = await client.list_tools()
                    names = [tool.name for tool in tools.tools]
                    result = await client.call_tool("delegate_code", self._arguments())
                    return names, result

            names, result = asyncio.run(run())

        self.assertEqual(names, ["delegate_code"])
        self.assertEqual(result.result_type, "complete")
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "accepted")
        self.assertFalse(result.structured_content.get("applied", False))

    def test_delegate_code_uses_server_default_profile_when_omitted(self):
        profiles = []
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            service = DelegationService(
                {"fixture": workspace},
                model_factory=lambda profile: profiles.append(profile.name) or FakeModel(),
                default_profile="qwen2.5-1.5b",
            )
            server = build_server(service)
            args = self._arguments()
            del args["model_profile"]

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    return await client.call_tool("delegate_code", args)

            result = asyncio.run(run())

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "accepted")
        self.assertEqual(profiles, ["qwen2.5-1.5b"])

    def test_delegate_code_thinking_override_reaches_profile(self):
        profiles = []
        with tempfile.TemporaryDirectory() as temp_dir:
            service = DelegationService(
                {"fixture": Path(temp_dir)},
                model_factory=lambda profile: profiles.append(profile.think) or FakeModel(),
            )
            server = build_server(service)

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    return await client.call_tool("delegate_code", {**self._arguments(), "think": True})

            result = asyncio.run(run())
        self.assertFalse(result.is_error)
        self.assertEqual(profiles, [True])

    def test_delegate_code_unknown_workspace_is_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = build_server(self._service(Path(temp_dir)))
            args = self._arguments()
            args["workspace_ref"] = "missing"

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    return await client.call_tool("delegate_code", args)

            result = asyncio.run(run())

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["kind"], "unknown_workspace")

    def test_discover_advertises_supported_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = build_server(self._service(Path(temp_dir)))

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    return client.protocol_version, client.server_info

            protocol_version, server_info = asyncio.run(run())

        self.assertEqual(protocol_version, "2026-07-28")
        self.assertEqual(server_info.name, "local-coding-agent")

    def test_process_bound_stdio_matches_in_process_result(self):
        helper = r'''
import json
import sys
from local_coding_agent.mcp_server import build_server
from local_coding_agent.service import DelegationService

class FakeModel:
    def chat(self, messages, *, tools=None):
        return {"message": {"role": "assistant", "content": json.dumps({
            "status": "candidate", "summary": "ok", "patch": "",
            "checks": [], "risks": [],
        }, ensure_ascii=False)}}

service = DelegationService({"fixture": sys.argv[1]}, model_factory=lambda p: FakeModel())
build_server(service).run(transport="stdio")
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")

            async def run_child():
                from mcp.client.stdio import StdioServerParameters, stdio_client
                from mcp.client.session import ClientSession

                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-c", helper, str(workspace)],
                    cwd=str(Path(__file__).parents[1]),
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.discover()
                        tools = await session.list_tools()
                        result = await session.call_tool("delegate_code", self._arguments())
                        return tools.tools, result

            tools, result = asyncio.run(run_child())

        self.assertEqual([tool.name for tool in tools], ["delegate_code"])
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "accepted")

    def test_sync_compatibility_path_does_not_block_async_event_loop(self):
        service = BlockingService()
        server = build_server(service)

        async def run():
            call = asyncio.create_task(
                server.call_tool(
                    "delegate_code",
                    {
                        "request_id": "blocking",
                        "workspace_ref": "fixture",
                        "model_profile": "qwen2.5-1.5b",
                        "task": {"id": "blocking", "goal": "read", "files": ["allowed.py"]},
                    },
                )
            )
            await asyncio.to_thread(service.started.wait, 1)
            started = time.monotonic()
            await asyncio.sleep(0.02)
            elapsed = time.monotonic() - started
            result = await call
            return elapsed, result

        elapsed, result = asyncio.run(run())

        self.assertLess(elapsed, 0.15)
        self.assertFalse(result.is_error)

    def test_sync_compatibility_path_rejects_when_bounded_gate_is_full(self):
        service = BlockingService()
        server = build_server(service, max_workers=1, max_queue=0)
        arguments = {
            "request_id": "blocking",
            "workspace_ref": "fixture",
            "model_profile": "qwen2.5-1.5b",
            "task": {"id": "blocking", "goal": "read", "files": ["allowed.py"]},
        }

        async def run():
            first = asyncio.create_task(server.call_tool("delegate_code", arguments))
            await asyncio.to_thread(service.started.wait, 1)
            second = await server.call_tool("delegate_code", {**arguments, "request_id": "second"})
            first_result = await first
            return first_result, second

        first, second = asyncio.run(run())

        self.assertFalse(first.is_error)
        self.assertTrue(second.is_error)
        self.assertEqual(second.structured_content["error"]["kind"], "queue_overload")

    def test_model_profile_mcp_resource(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = build_server(self._service(Path(temp_dir)))

            async def run():
                from mcp.client import Client

                async with Client(server) as client:
                    resources = await client.list_resources()
                    uris = [str(res.uri) for res in resources.resources]
                    content = await client.read_resource("model://profile")
                    return uris, content

            uris, content = asyncio.run(run())

        self.assertIn("model://profile", uris)
        self.assertTrue(len(content.contents) > 0)
        parsed = json.loads(content.contents[0].text)
        self.assertIn("profiles", parsed)


if __name__ == "__main__":
    unittest.main()

