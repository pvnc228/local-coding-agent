import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_coding_agent.service import DelegationRequest, DelegationService
from local_coding_agent.stdio import StdioDelegationAdapter
from local_coding_agent.task import TaskEnvelope


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


class StdioDelegationAdapterTests(unittest.TestCase):
    def _request_mapping(self):
        return {
            "request_id": "stdio-request-1",
            "workspace_ref": "fixture",
            "model_profile": "qwen2.5-1.5b",
            "task": {
                "id": "stdio-task-1",
                "goal": "прочитать разрешённый файл",
                "files": ["allowed.py"],
            },
        }

    def _service(self, workspace: Path) -> DelegationService:
        return DelegationService(
            {"fixture": workspace},
            model_factory=lambda profile: FakeModel(),
        )

    def test_delegate_code_jsonl_matches_direct_service_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            request_mapping = self._request_mapping()
            direct = self._service(workspace).delegate(
                "trusted-host", DelegationRequest.from_mapping(request_mapping)
            )
            line = json.dumps(
                {
                    "method": "delegate_code",
                    "caller_id": "trusted-host",
                    "params": request_mapping,
                },
                ensure_ascii=False,
            )

            result = json.loads(StdioDelegationAdapter(self._service(workspace)).handle_line(line))

        self.assertEqual(result, direct)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["summary"], "готово")
        self.assertFalse(result["applied"])

    def test_process_bound_stdio_adapter_matches_direct_service(self):
        helper = r'''
import json
import sys

from local_coding_agent.service import DelegationService
from local_coding_agent.stdio import StdioDelegationAdapter


class FakeModel:
    def chat(self, messages, *, tools=None):
        return {"message": {"role": "assistant", "content": json.dumps({
            "status": "candidate",
            "summary": "готово",
            "patch": "",
            "checks": [],
            "risks": [],
        }, ensure_ascii=False)}}


service = DelegationService(
    {"fixture": sys.argv[1]},
    model_factory=lambda profile: FakeModel(),
)
StdioDelegationAdapter(service).serve()
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
            request_mapping = self._request_mapping()
            direct = self._service(workspace).delegate(
                "trusted-host", DelegationRequest.from_mapping(request_mapping)
            )
            line = json.dumps(
                {
                    "method": "delegate_code",
                    "caller_id": "trusted-host",
                    "params": request_mapping,
                },
                ensure_ascii=False,
            )
            completed = subprocess.run(
                [sys.executable, "-c", helper, str(workspace)],
                cwd=Path(__file__).parents[2],
                input=(line + "\n").encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        self.assertEqual(json.loads(completed.stdout.decode("utf-8")), direct)

    def test_protocol_rejects_unknown_method_without_calling_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = json.loads(
                StdioDelegationAdapter(self._service(Path(temp_dir))).handle_line(
                    json.dumps({"method": "unknown", "params": {}})
                )
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "unknown_method")

    def test_protocol_rejects_oversized_request_before_json_decode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = StdioDelegationAdapter(self._service(Path(temp_dir)), max_request_bytes=16)
            result = json.loads(adapter.handle_line(b"{" + b"x" * 16))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["kind"], "request_too_large")


if __name__ == "__main__":
    unittest.main()
