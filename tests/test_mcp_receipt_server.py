"""Focused tests for the private MCP receipt facade.

Fixtures are assembled as canonical receipt bytes.  No receipt writer or
legacy command is invoked by this test module.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CANDIDATE = Path(__file__).resolve().parents[1]
TARGET = CANDIDATE.parent
HARNESS_TEMP = TARGET / "harness" / "temp"
SERVER_PATH = CANDIDATE / "mcp_receipt_server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("test_mcp_receipt_server_impl", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("server module has no import loader")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server_module()


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _identity(path: Path):
    data = path.read_bytes()
    return {
        "status": "present",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "stable_during_hash": True,
    }


def _receipt(
    workspace: Path,
    receipt_rel: str,
    *,
    schema: str = "origin-result-receipt/v2",
    input_rel: str = "input.txt",
    artifact_rel: str = "artifact.txt",
    create_files: bool = True,
    execution_status: str = "completed",
) -> Path:
    if create_files:
        (workspace / input_rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / artifact_rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / input_rel).write_text("fixture input\n", encoding="utf-8")
        (workspace / artifact_rel).write_text("fixture artifact\n", encoding="utf-8")

    input_path = workspace / input_rel
    artifact_path = workspace / artifact_rel
    missing_input = {
        "status": "missing"
    }
    input_identity = _identity(input_path) if create_files else missing_input
    artifact_identity = _identity(artifact_path) if create_files else {"status": "missing"}
    execution = {
        "argv": ["fixture", "--recorded-only"],
        "status": execution_status,
        "exit_code": 0 if execution_status == "completed" else None,
        "timed_out": False,
        "launch_error": None
        if execution_status == "completed"
        else {"type": "FixtureLaunchError", "message": "child status is recorded data"},
        "started_at_utc": "2026-10-18T00:00:00.000Z",
        "finished_at_utc": "2026-10-18T00:00:00.001Z",
        "elapsed_ms": 1,
        "timeout_seconds": 1.0,
        "stdout": {"text": "", "bytes_total": 0, "truncated": False},
        "stderr": {"text": "", "bytes_total": 0, "truncated": False},
    }
    body = {
        "schema": schema,
        "run_id": "0123456789abcdef0123456789abcdef",
        "recorded_at_utc": "2026-10-18T00:00:00.001Z",
        "scope": {
            "inputs": [input_rel],
            "artifacts": [artifact_rel],
            "binding": "BOUND",
        },
        "execution": execution,
        "inputs_before": {input_rel: input_identity},
        "inputs_after": {input_rel: input_identity},
        "artifacts_after": {artifact_rel: artifact_identity},
    }
    if schema == "origin-result-receipt/v2":
        body["producer"] = {
            "name": "fixture-producer",
            "software_version": "0.0-fixture",
            "repository": "fixture://producer",
            "format_documentation": "fixture-receipt.json",
        }
    target = workspace / receipt_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    target.write_bytes(_canonical(envelope) + b"\n")
    return target


class ReceiptBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        HARNESS_TEMP.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HARNESS_TEMP, prefix="case-")
        self.workspace = Path(self.temp.name)
        self.bridge = server.ReceiptBridge(str(self.workspace))
        self._initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _initialize(self):
        response = self.bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "focused-test", "version": "1"},
                },
            }
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")
        self.assertNotIn("instructions", response["result"])
        self.assertIsNone(
            self.bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def _call(self, arguments):
        response = self.bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "yumi_check_receipt", "arguments": arguments},
            }
        )
        result = response["result"]
        text = result["content"][0]["text"]
        self.assertEqual(json.loads(text), result["structuredContent"])
        return result

    def test_handshake_list_and_readonly_tool(self):
        response = self.bridge.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(response["result"], {})
        response = self.bridge.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "yumi_check_receipt")
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        self.assertFalse(tools[0]["annotations"]["destructiveHint"])
        self.assertEqual(tools[0]["inputSchema"]["required"], ["receipt", "inputs", "artifacts"])
        self.assertFalse(tools[0]["inputSchema"]["additionalProperties"])

    def test_v1_v2_match_and_child_status_are_non_endorsement(self):
        _receipt(self.workspace, "v1.json", schema="origin-result-receipt/v1")
        result = self._call({"receipt": "v1.json", "inputs": ["input.txt"], "artifacts": ["artifact.txt"]})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["scope_state"], "MATCH")
        self.assertEqual(result["structuredContent"]["producer_metadata_status"], "not_recorded")

        _receipt(self.workspace, "v2.json", schema="origin-result-receipt/v2")
        result = self._call({"receipt": "v2.json", "inputs": ["input.txt"], "artifacts": ["artifact.txt"]})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["producer_metadata_status"], "self_reported")
        self.assertIn("not authentication", result["structuredContent"]["producer_metadata_limit"])

        _receipt(self.workspace, "child.json", execution_status="launch_error")
        result = self._call({"receipt": "child.json", "inputs": ["input.txt"], "artifacts": ["artifact.txt"]})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["execution_status"], "launch_error")
        self.assertEqual(result["structuredContent"]["scope_state"], "MATCH")

    def test_invalid_receipt_bad_arguments_and_path_escape(self):
        bad = self.workspace / "bad.json"
        bad.write_text('{"body":{},"sha256":"' + "0" * 64 + '"}\n', encoding="utf-8")
        result = self._call({"receipt": "bad.json", "inputs": [], "artifacts": []})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error_category"], "invalid_receipt")

        missing = self.bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "yumi_check_receipt", "arguments": {"receipt": "bad.json"}},
            }
        )
        self.assertTrue(missing["result"]["isError"])
        self.assertEqual(missing["result"]["structuredContent"]["error_category"], "invalid_arguments")

        escaped = self.bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "yumi_check_receipt",
                    "arguments": {"receipt": "../bad.json", "inputs": [], "artifacts": []},
                },
            }
        )
        self.assertTrue(escaped["result"]["isError"])
        self.assertEqual(escaped["result"]["structuredContent"]["error_category"], "invalid_arguments")

    def test_scope_guard_prevents_undeclared_referenced_reads(self):
        _receipt(
            self.workspace,
            "guard.json",
            input_rel="secret.txt",
            artifact_rel="declared.out",
            create_files=False,
        )
        original_identity = self.bridge.origin._file_identity
        original_check = self.bridge.origin.check_receipt

        def fail_identity(*args, **kwargs):
            raise AssertionError("referenced identity was read before scope guard")

        def fail_check(*args, **kwargs):
            raise AssertionError("origin checker was called on a scope mismatch")

        self.bridge.origin._file_identity = fail_identity
        self.bridge.origin.check_receipt = fail_check
        try:
            result = self._call(
                {"receipt": "guard.json", "inputs": ["other.txt"], "artifacts": ["declared.out"]}
            )
        finally:
            self.bridge.origin._file_identity = original_identity
            self.bridge.origin.check_receipt = original_check
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["scope_state"], "MISMATCH")
        self.assertFalse(result["structuredContent"]["referenced_files_read"])

    def test_metadata_is_data_and_frame_validation_is_bounded(self):
        _receipt(self.workspace, "metadata.json")
        result = self._call({"receipt": "metadata.json", "inputs": ["input.txt"], "artifacts": ["artifact.txt"]})
        metadata = result["structuredContent"]["producer_metadata"]
        self.assertEqual(metadata["name"], "fixture-producer")
        self.assertNotIn("instructions", metadata)
        self.assertIn("informational", result["structuredContent"]["producer_metadata_limit"].lower())

        duplicate = b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"ping"}\n'
        with self.assertRaises(ValueError):
            server._parse_frame(duplicate)
        with self.assertRaises(ValueError):
            server._parse_frame(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":NaN}\n')
        with self.assertRaises(ValueError):
            server._parse_frame(b"{}")
        with self.assertRaises(ValueError):
            server._parse_frame(b"x" * (server.MAX_FRAME_BYTES + 1) + b"\n")
        with self.assertRaises(server.ProtocolError) as invalid_id:
            self.bridge.handle({"jsonrpc": "2.0", "id": True, "method": "ping"})
        self.assertEqual(invalid_id.exception.code, -32600)
        with self.assertRaises(server.ProtocolError) as invalid_params:
            self.bridge.handle({"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {"extra": 1}})
        self.assertEqual(invalid_params.exception.code, -32602)

    def test_one_finite_stdio_wire_probe(self):
        _receipt(self.workspace, "wire.json")
        env = os.environ.copy()
        env["TMP"] = str(HARNESS_TEMP)
        env["TEMP"] = str(HARNESS_TEMP)
        env["TMPDIR"] = str(HARNESS_TEMP)
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "wire", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "yumi_check_receipt",
                    "arguments": {"receipt": "wire.json", "inputs": ["input.txt"], "artifacts": ["artifact.txt"]},
                },
            },
        ]
        wire = b"".join(_canonical(message) + b"\n" for message in messages)
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-S", str(SERVER_PATH), "--workspace", str(self.workspace)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            output, error = process.communicate(wire, timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, error = process.communicate()
            self.fail("stdio wire probe did not reach EOF within 10 seconds")
        self.assertEqual(process.returncode, 0, error.decode("utf-8", errors="replace"))
        self.assertEqual(error, b"")
        responses = [json.loads(line) for line in output.splitlines() if line]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3, 4])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(responses[2]["result"]["tools"]), 1)
        self.assertFalse(responses[3]["result"]["isError"])


class ReceiptBridgeRepairTests(unittest.TestCase):
    """Durable unit regressions for the five post-suite repair invariants."""

    @classmethod
    def setUpClass(cls):
        HARNESS_TEMP.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HARNESS_TEMP, prefix="repair-case-")
        self.workspace = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_repair_snapshot_binding_uses_validated_receipt_body(self):
        _receipt(
            self.workspace,
            "receipt.json",
            input_rel="input-a.txt",
            artifact_rel="artifact-a.txt",
        )
        bridge = server.ReceiptBridge(str(self.workspace))
        original_loader = bridge.origin._load_receipt
        calls = 0

        def replacing_loader(root, requested):
            nonlocal calls
            loaded = original_loader(root, requested)
            calls += 1
            if calls == 1:
                _receipt(
                    self.workspace,
                    "receipt.json",
                    input_rel="input-b.txt",
                    artifact_rel="artifact-b.txt",
                )
            return loaded

        bridge.origin._load_receipt = replacing_loader
        try:
            result = bridge._call_tool(
                {
                    "name": "yumi_check_receipt",
                    "arguments": {
                        "receipt": "receipt.json",
                        "inputs": ["input-a.txt"],
                        "artifacts": ["artifact-a.txt"],
                    },
                },
                2,
            )
        finally:
            bridge.origin._load_receipt = original_loader
        self.assertEqual(calls, 1)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["scope_state"], "MATCH")
        self.assertEqual(result["structuredContent"]["differences"], [])

    def test_repair_origin_executes_exact_hashed_bytes_once(self):
        origin_path = CANDIDATE / "origin_receipt.py"
        raw = origin_path.read_bytes()
        original_compile = getattr(server, "compile", builtins.compile)
        seen = []

        def compile_checked(source, filename, mode, *args, **kwargs):
            self.assertEqual(source, raw)
            seen.append(len(source))
            return builtins.compile(source, filename, mode, *args, **kwargs)

        server.compile = compile_checked
        try:
            loaded = server._load_origin()
        finally:
            server.compile = original_compile
        self.assertEqual(seen, [len(raw)])
        self.assertEqual(loaded.SOFTWARE_VERSION, server.EXPECTED_ORIGIN_VERSION)

    def test_repair_metadata_and_protocol_negotiation_are_inert(self):
        bridge = server.ReceiptBridge(str(self.workspace))
        early_ping = bridge.handle(
            {"jsonrpc": "2.0", "id": 0, "method": "ping", "params": {"_meta": {"early": True}}}
        )
        self.assertEqual(early_ping["result"], {})
        response = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "repair-client",
                        "version": "1",
                        "title": "Inert title",
                        "description": "Inert description",
                        "websiteUrl": "https://example.invalid/client",
                        "icons": [
                            {
                                "src": "https://example.invalid/icon.png",
                                "mimeType": "image/png",
                                "sizes": ["64x64"],
                                "theme": "light",
                            }
                        ],
                    },
                    "_meta": {"caller": "repair"},
                },
            }
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")
        self.assertIsNone(
            bridge.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": {}}}
            )
        )
        listed = bridge.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": {}}}
        )
        self.assertEqual(len(listed["result"]["tools"]), 1)

    def test_repair_scope_bounds_precede_origin_canonicalization(self):
        bridge = server.ReceiptBridge(str(self.workspace))
        original_scope_list = bridge.origin._scope_list

        def fail_scope_list(*args, **kwargs):
            raise AssertionError("core canonicalization ran before duplicate/max validation")

        bridge.origin._scope_list = fail_scope_list
        try:
            duplicate = bridge._call_tool(
                {
                    "name": "yumi_check_receipt",
                    "arguments": {"receipt": "r.json", "inputs": ["a", "a"], "artifacts": []},
                },
                4,
            )
            too_many = bridge._call_tool(
                {
                    "name": "yumi_check_receipt",
                    "arguments": {
                        "receipt": "r.json",
                        "inputs": [f"p{i}" for i in range(129)],
                        "artifacts": [],
                    },
                },
                5,
            )
        finally:
            bridge.origin._scope_list = original_scope_list
        self.assertTrue(duplicate["isError"])
        self.assertEqual(duplicate["structuredContent"]["error_category"], "invalid_arguments")
        self.assertTrue(too_many["isError"])
        self.assertEqual(too_many["structuredContent"]["error_category"], "invalid_arguments")

    def test_repair_oversize_response_preserves_request_id(self):
        class Input:
            def __init__(self):
                self.buffer = io.BytesIO(b"{}\n")

        class Output:
            def __init__(self):
                self.buffer = io.BytesIO()

        class FakeServer:
            def handle(self, message):
                return {"jsonrpc": "2.0", "id": 77, "result": {"blob": "x" * (server.MAX_FRAME_BYTES + 1)}}

        old_stdin, old_stdout = server.sys.stdin, server.sys.stdout
        output = Output()
        server.sys.stdin, server.sys.stdout = Input(), output
        try:
            server.serve_stdio(FakeServer())
        finally:
            server.sys.stdin, server.sys.stdout = old_stdin, old_stdout
        response = json.loads(output.buffer.getvalue().decode("utf-8"))
        self.assertEqual(response["id"], 77)
        self.assertEqual(response["error"]["code"], -32603)


if __name__ == "__main__":
    unittest.main()
