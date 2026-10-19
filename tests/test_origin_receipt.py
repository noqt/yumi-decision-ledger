"""Focused regressions. Fake Popen/identity values below are test fixtures, not execution evidence."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Running this file directly places only tests/ on sys.path; load the adjacent CLI.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import origin_receipt as origin


class _FakeProcess:
    """In-memory subprocess-shaped fixture; it does not launch a command."""

    def __init__(self):
        self.stdout = io.BytesIO(b"fixture stdout\n")
        self.stderr = io.BytesIO(b"fixture stderr\n")
        self.returncode = None

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


class ConservativeScopeTests(unittest.TestCase):
    def _capture_fixture(self, root: Path, *, inputs: list[str], artifacts: list[str]) -> Path:
        """Create disposable shape/identity data with Popen mocked; never product evidence."""
        (root / "receipts").mkdir(exist_ok=True)
        with patch.object(origin.subprocess, "Popen", return_value=_FakeProcess()):
            origin.run_command(
                str(root),
                "receipts/fixture.json",
                inputs,
                artifacts,
                ["synthetic-command", "--not-run"],
                2,
            )
        return root / "receipts/fixture.json"

    def test_artifact_only_capture_is_unbound_but_keeps_observed_facts(self):
        with tempfile.TemporaryDirectory(prefix="scope-only-", dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            artifact = root / "output.bin"
            artifact.write_bytes(b"fixture artifact")
            receipt = self._capture_fixture(root, inputs=[], artifacts=["output.bin"])
            body = json.loads(receipt.read_text(encoding="utf-8"))["body"]

            # This mock exercises only receipt production/schema; its exit is not a real command result.
            self.assertEqual(body["scope"]["binding"], "UNBOUND")
            self.assertEqual(body["execution"]["status"], "completed")
            self.assertEqual(body["execution"]["exit_code"], 0)
            self.assertEqual(body["execution"]["stdout"]["text"], "fixture stdout\n")
            self.assertEqual(body["artifacts_after"]["output.bin"]["status"], "present")

            with patch.object(origin.subprocess, "Popen", side_effect=AssertionError("check tried to execute data")):
                checked = origin.check_receipt(
                    str(root), "receipts/fixture.json", [], ["output.bin"]
                )
            self.assertEqual(checked["scope_state"], "UNBOUND")
            self.assertEqual(checked["declared_scope_state"], "UNBOUND")
            self.assertEqual(checked["differences"], [])
            self.assertEqual(checked["recorded_exit_code"], 0)

    def test_unverifiable_recorded_or_current_identity_is_indeterminate(self):
        with tempfile.TemporaryDirectory(prefix="identity-check-", dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("fixture input", encoding="utf-8")
            (root / "output.bin").write_bytes(b"fixture output")
            receipt = self._capture_fixture(root, inputs=["input.txt"], artifacts=["output.bin"])
            original = json.loads(receipt.read_text(encoding="utf-8"))

            def write_body(body, receipt_name):
                envelope = {
                    "body": body,
                    "sha256": hashlib.sha256(origin._canonical(body)).hexdigest(),
                }
                target = root / "receipts" / receipt_name
                with target.open("xb") as handle:
                    handle.write(origin._canonical(envelope) + b"\n")
                return f"receipts/{receipt_name}"

            def mutate_recorded_input(body):
                body["inputs_before"]["input.txt"] = {"status": "too_large", "bytes": origin.MAX_FILE_BYTES + 1}
                body["inputs_after"]["input.txt"] = {"status": "too_large", "bytes": origin.MAX_FILE_BYTES + 1}

            def mutate_recorded_artifact(body):
                body["artifacts_after"]["output.bin"] = {"status": "not_regular_file"}

            def mutate_unstable_recorded_input(body):
                body["inputs_before"]["input.txt"]["stable_during_hash"] = False
                body["inputs_after"]["input.txt"]["stable_during_hash"] = False

            cases = (
                ("recorded too_large input", mutate_recorded_input, None),
                ("recorded non-file artifact", mutate_recorded_artifact, None),
                ("recorded unstable input", mutate_unstable_recorded_input, None),
                (
                    "current too_large identities",
                    None,
                    [
                        {"status": "too_large", "bytes": origin.MAX_FILE_BYTES + 1},
                        {"status": "too_large", "bytes": origin.MAX_FILE_BYTES + 1},
                    ],
                ),
                (
                    "current unstable identities",
                    None,
                    [
                        dict(original["body"]["inputs_after"]["input.txt"], stable_during_hash=False),
                        dict(original["body"]["artifacts_after"]["output.bin"], stable_during_hash=False),
                    ],
                ),
            )
            for index, (label, mutation, current_values) in enumerate(cases):
                with self.subTest(case=label):
                    body = copy.deepcopy(original["body"])
                    if mutation is not None:
                        mutation(body)
                    receipt_rel = write_body(body, f"scenario-{index}.json")
                    with patch.object(origin.subprocess, "Popen", side_effect=AssertionError("check replayed argv")):
                        if current_values is None:
                            checked = origin.check_receipt(
                                str(root), receipt_rel, ["input.txt"], ["output.bin"]
                            )
                        else:
                            # Patched identities exercise rare size/unstable branches without large files or race claims.
                            with patch.object(origin, "_file_identity", side_effect=current_values):
                                checked = origin.check_receipt(
                                    str(root), receipt_rel, ["input.txt"], ["output.bin"]
                                )
                    kinds = {item["kind"] for item in checked["differences"]}
                    self.assertEqual(checked["scope_state"], "INDETERMINATE")
                    self.assertIn("unverifiable_identity", kinds)
                    self.assertEqual(checked["execution_status"], "completed")
                    self.assertEqual(checked["recorded_exit_code"], 0)

    def test_missing_and_changed_files_keep_explicit_differences(self):
        with tempfile.TemporaryDirectory(prefix="path-change-", dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("before", encoding="utf-8")
            (root / "output.bin").write_bytes(b"output")
            self._capture_fixture(root, inputs=["input.txt"], artifacts=["output.bin"])

            (root / "input.txt").write_text("changed", encoding="utf-8")
            (root / "output.bin").write_bytes(b"changed output")
            changed = origin.check_receipt(
                str(root), "receipts/fixture.json", ["input.txt"], ["output.bin"]
            )
            self.assertEqual(changed["scope_state"], "MISMATCH")
            self.assertIn(
                {"path": "input.txt", "kind": "changed_since_run"}, changed["differences"]
            )
            self.assertIn(
                {"path": "output.bin", "kind": "changed_since_run"}, changed["differences"]
            )

            (root / "input.txt").unlink()
            (root / "output.bin").unlink()
            missing = origin.check_receipt(
                str(root), "receipts/fixture.json", ["input.txt"], ["output.bin"]
            )
            kinds = {(item["path"], item["kind"]) for item in missing["differences"]}
            self.assertEqual(missing["scope_state"], "INDETERMINATE")
            self.assertIn(("input.txt", "missing_input"), kinds)
            self.assertIn(("input.txt", "unverifiable_identity"), kinds)
            self.assertIn(("output.bin", "missing_artifact"), kinds)
            self.assertIn(("output.bin", "unverifiable_identity"), kinds)


class SelfDescribingReceiptShapeTests(unittest.TestCase):
    """Synthetic shape fixtures only; these tests perform no capture or I/O."""

    @staticmethod
    def _body(schema):
        body = {
            "schema": schema,
            "run_id": "a" * 32,
            "recorded_at_utc": "2026-10-17T00:00:00.000Z",
            "scope": {"inputs": [], "artifacts": [], "binding": "UNBOUND"},
            "execution": {
                "argv": ["synthetic-command"],
                "status": "completed",
                "exit_code": 0,
                "timed_out": False,
                "launch_error": None,
                "started_at_utc": "2026-10-17T00:00:00.000Z",
                "finished_at_utc": "2026-10-17T00:00:00.000Z",
                "elapsed_ms": 0,
                "timeout_seconds": 1.0,
                "stdout": {"text": "", "bytes_total": 0, "truncated": False},
                "stderr": {"text": "", "bytes_total": 0, "truncated": False},
            },
            "inputs_before": {},
            "inputs_after": {},
            "artifacts_after": {},
        }
        if schema in {origin.SCHEMA_V2, origin.SCHEMA_V3}:
            body["producer"] = dict(origin.PRODUCER_METADATA)
        if schema == origin.SCHEMA_V3:
            for name in ("stdout", "stderr"):
                body["execution"][name].update(
                    {
                        "complete": True,
                        "total_known": True,
                        "capture_state": "complete",
                        "cutoff_reason": None,
                    }
                )
        return body

    def test_v1_without_producer_and_v2_with_producer_are_accepted(self):
        legacy = self._body(origin.SCHEMA_V1)
        origin._check_body_shape(legacy)
        self.assertNotIn("producer", legacy)

        current = self._body(origin.SCHEMA_V2)
        origin._check_body_shape(current)
        self.assertEqual(set(current["producer"]), set(origin.PRODUCER_FIELDS))

        repaired = self._body(origin.SCHEMA_V3)
        origin._check_body_shape(repaired)
        self.assertEqual(repaired["execution"]["stdout"]["capture_state"], "complete")

    def test_legacy_capture_has_no_invented_completeness(self):
        legacy = self._body(origin.SCHEMA_V2)
        with tempfile.TemporaryDirectory(prefix="legacy-check-", dir=ROOT) as tmp:
            root = Path(tmp)
            (root / "receipts").mkdir()
            envelope = {
                "body": legacy,
                "sha256": hashlib.sha256(origin._canonical(legacy)).hexdigest(),
            }
            (root / "receipts" / "legacy.json").write_bytes(origin._canonical(envelope) + b"\n")
            checked = origin.check_receipt(str(root), "receipts/legacy.json", [], [])
        self.assertEqual(
            checked["capture_completeness"]["stdout"],
            {"state": "legacy_unrecorded", "complete": False, "total_known": False},
        )

    def test_schema_requires_exact_producer_shape(self):
        missing = self._body(origin.SCHEMA_V2)
        del missing["producer"]
        with self.assertRaises(origin.ReceiptError):
            origin._check_body_shape(missing)

        extra = self._body(origin.SCHEMA_V1)
        extra["producer"] = dict(origin.PRODUCER_METADATA)
        with self.assertRaises(origin.ReceiptError):
            origin._check_body_shape(extra)

        malformed = self._body(origin.SCHEMA_V2)
        del malformed["producer"]["software_version"]
        with self.assertRaises(origin.ReceiptError):
            origin._check_body_shape(malformed)

    def test_producer_bounds_and_locator_values_are_data(self):
        metadata = dict(origin.PRODUCER_METADATA)
        metadata["repository"] = "python -c must-not-run"
        origin._check_producer_metadata(metadata)
        self.assertEqual(metadata["repository"], "python -c must-not-run")

        for field, bad_value in (
            ("repository", "x" * (origin.PRODUCER_FIELDS["repository"] + 1)),
            ("name", " padded "),
            ("format_documentation", "line\nbreak"),
        ):
            invalid = dict(origin.PRODUCER_METADATA)
            invalid[field] = bad_value
            with self.subTest(field=field):
                with self.assertRaises(origin.ReceiptError):
                    origin._check_producer_metadata(invalid)

class InheritedPipeDeadlineRegressionTests(unittest.TestCase):
    """Actual CLI regressions; helper processes are short-lived and self-owned."""

    RETURN_SCHEDULING_MARGIN_SECONDS = 1.5

    @staticmethod
    def _helper_command(
        root: Path, *, helper_seconds: float, parent_seconds: float, wait_ready: bool = False
    ) -> tuple[list[str], Path]:
        helper_done = root / "helper.done"
        helper_pid = root / "helper.pid"
        parent_ready = root / "parent.ready"
        helper_code = (
            "import os,pathlib,sys,time; "
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='ascii'); "
            "time.sleep(float(sys.argv[2])); "
            "pathlib.Path(sys.argv[3]).write_text('done', encoding='ascii')"
        )
        if wait_ready:
            parent_code = (
                "import pathlib,subprocess,sys,time\n"
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False)\n"
                "deadline=time.monotonic()+2\n"
                "while not pathlib.Path(sys.argv[5]).exists() and time.monotonic()<deadline:\n"
                "    time.sleep(0.01)\n"
                "pathlib.Path(sys.argv[5]).write_text('ready', encoding='ascii')\n"
                "print('parent', flush=True)\n"
                "time.sleep(float(sys.argv[6]))"
            )
            command = [
                sys.executable,
                "-c",
                parent_code,
                helper_code,
                str(helper_pid),
                str(helper_seconds),
                str(helper_done),
                str(parent_ready),
                str(parent_seconds),
            ]
        else:
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False); "
                "print('parent', flush=True); "
                "time.sleep(float(sys.argv[5]))"
            )
            command = [
                sys.executable,
                "-c",
                parent_code,
                helper_code,
                str(helper_pid),
                str(helper_seconds),
                str(helper_done),
                str(parent_seconds),
            ]
        return command, helper_done

    def _run_cli(self, root: Path, receipt_name: str, command: list[str], timeout: float):
        receipt = root / receipt_name
        started = time.monotonic()
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "origin_receipt.py"),
                "run",
                "--workspace",
                str(root),
                "--receipt",
                receipt_name,
                "--timeout",
                str(timeout),
                "--",
                *command,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(receipt.is_file())
        body = json.loads(receipt.read_text(encoding="utf-8"))["body"]
        return elapsed, completed.stdout, body

    @staticmethod
    def _wait_for_helper(done: Path) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not done.exists():
            time.sleep(0.02)
        if not done.exists():
            raise AssertionError("self-created helper exceeded its bounded lifetime")

    def test_direct_exit_with_inherited_pipe_publishes_incomplete_receipt(self):
        with tempfile.TemporaryDirectory(prefix="direct-exit-pipe-", dir=ROOT) as tmp:
            root = Path(tmp)
            (root / "receipts").mkdir()
            command, done = self._helper_command(root, helper_seconds=5.0, parent_seconds=0.0)
            elapsed, raw_stdout, body = self._run_cli(root, "receipts/direct.json", command, 3.0)
            self.assertFalse(done.exists(), "helper completed before the bounded direct return")
            self._wait_for_helper(done)
        self.assertLess(
            elapsed,
            origin.CAPTURE_DEADLINE_SECONDS + self.RETURN_SCHEDULING_MARGIN_SECONDS,
        )
        self.assertIn('"status": "completed"', raw_stdout)
        self.assertFalse(body["execution"]["timed_out"])
        self.assertEqual(body["execution"]["status"], "completed")
        self.assertEqual(body["execution"]["stdout"]["capture_state"], "deadline")
        self.assertFalse(body["execution"]["stdout"]["complete"])
        self.assertFalse(body["execution"]["stdout"]["total_known"])

    def test_timeout_with_inherited_pipe_publishes_timeout_and_incomplete_receipt(self):
        with tempfile.TemporaryDirectory(prefix="timeout-pipe-", dir=ROOT) as tmp:
            root = Path(tmp)
            (root / "receipts").mkdir()
            command, done = self._helper_command(
                root, helper_seconds=5.0, parent_seconds=5.0, wait_ready=True
            )
            elapsed, raw_stdout, body = self._run_cli(root, "receipts/timeout.json", command, 2.5)
            self.assertFalse(done.exists(), "helper completed before the bounded timeout return")
            self._wait_for_helper(done)
        self.assertLess(
            elapsed,
            2.5 + origin.CAPTURE_DEADLINE_SECONDS + self.RETURN_SCHEDULING_MARGIN_SECONDS,
        )
        self.assertIn('"status": "timed_out"', raw_stdout)
        self.assertTrue(body["execution"]["timed_out"])
        self.assertEqual(body["execution"]["status"], "timed_out")
        self.assertEqual(body["execution"]["stdout"]["capture_state"], "deadline")
        self.assertFalse(body["execution"]["stdout"]["complete"])
        self.assertFalse(body["execution"]["stdout"]["total_known"])

    def test_finite_output_is_captured_completely(self):
        with tempfile.TemporaryDirectory(prefix="finite-output-", dir=ROOT) as tmp:
            root = Path(tmp)
            (root / "receipts").mkdir()
            command = [sys.executable, "-c", "print('finite')"]
            elapsed, raw_stdout, body = self._run_cli(root, "receipts/finite.json", command, 1.0)
        self.assertLess(elapsed, 5)
        self.assertIn('"status": "completed"', raw_stdout)
        self.assertEqual(body["execution"]["status"], "completed")
        self.assertEqual(body["execution"]["stdout"]["text"], f"finite{os.linesep}")
        self.assertEqual(body["execution"]["stdout"]["capture_state"], "complete")
        self.assertTrue(body["execution"]["stdout"]["complete"])
        self.assertTrue(body["execution"]["stdout"]["total_known"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
