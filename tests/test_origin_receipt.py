"""Focused regressions. Fake Popen/identity values below are test fixtures, not execution evidence."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
