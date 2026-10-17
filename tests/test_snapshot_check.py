"""Focused actual-CLI checks for the bounded snapshot applicability bridge."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "snapshot_check.py"
INPUT_LIMIT = 64 * 1024
RESULT_LIMIT = 16 * 1024


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot(value="by-bed", *, scope="fictional-greenhouse-console", key="sort_mode"):
    return {"scope": scope, "settings": {key: value}}


def one_setting_ledger(value="by-bed", *, scope="fictional-greenhouse-console", key="sort_mode"):
    return {
        "scope": scope,
        "required_keys": [key],
        "revisions": [
            {
                "id": "revision-current",
                "source_id": "source-greenhouse-guide",
                "assertions": [
                    {
                        "id": "assertion-sort-mode",
                        "key": key,
                        "value": value,
                        "source_id": "source-greenhouse-guide",
                    }
                ],
            }
        ],
    }


class SnapshotCheckCliCases(unittest.TestCase):
    def setUp(self):
        temp_root = os.environ.get("TEMP") or os.environ.get("TMP") or str(ROOT)
        self.temp = tempfile.TemporaryDirectory(prefix="snapshot-check-test-", dir=temp_root)
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, snapshot_raw, ledger_raw):
        snapshot_path = self.directory / "snapshot.json"
        ledger_path = self.directory / "ledger.json"
        snapshot_path.write_bytes(snapshot_raw)
        ledger_path.write_bytes(ledger_raw)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                str(SCRIPT),
                "check",
                "--snapshot",
                str(snapshot_path),
                "--ledger",
                str(ledger_path),
            ],
            cwd=self.directory,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.stderr, b"")
        self.assertLessEqual(len(completed.stdout), RESULT_LIMIT + 1)
        self.assertTrue(completed.stdout.endswith(b"\n"))
        result = json.loads(completed.stdout)
        self.assertLessEqual(len(canonical_bytes(result)), RESULT_LIMIT)
        return completed.returncode, result, completed.stdout

    def test_unchanged_reports_actual_input_digests_and_typed_match(self):
        snapshot_raw = canonical_bytes(snapshot())
        ledger_raw = canonical_bytes(one_setting_ledger())

        code, result, _ = self.invoke(snapshot_raw, ledger_raw)

        self.assertEqual(code, 0)
        self.assertEqual((result["status"], result["reason"]), ("UNCHANGED", "snapshot_settings_match"))
        self.assertTrue(result["scope_matches"])
        self.assertTrue(result["settings_keys_match"])
        self.assertEqual(result["inputs"]["snapshot"]["sha256"], hashlib.sha256(snapshot_raw).hexdigest())
        self.assertEqual(result["inputs"]["ledger"]["sha256"], hashlib.sha256(ledger_raw).hexdigest())
        self.assertTrue(result["inputs"]["snapshot"]["complete"])
        self.assertTrue(result["inputs"]["ledger"]["complete"])
        drift = result["key_drift"][0]
        self.assertEqual(drift["key"], "sort_mode")
        self.assertEqual(drift["current_status"], "stable")
        self.assertTrue(drift["value_matches"])
        self.assertEqual(drift["assertion_ids"], ["assertion-sort-mode"])
        self.assertEqual(drift["revision_ids"], ["revision-current"])
        self.assertEqual(drift["source_ids"], ["source-greenhouse-guide"])
        self.assertEqual(result["capability"]["capability"], "snapshot-applicability-check")
        self.assertEqual(result["claims"]["snapshot_authentication"], "not_performed")

    def test_linear_supersession_returns_exact_current_key_delta(self):
        ledger = {
            "scope": "fictional-greenhouse-console",
            "required_keys": ["sort_mode"],
            "revisions": [
                {
                    "id": "revision-initial",
                    "source_id": "source-first-handbook",
                    "assertions": [
                        {
                            "id": "assertion-initial-sort",
                            "key": "sort_mode",
                            "value": "by-bed",
                            "source_id": "source-first-handbook",
                        }
                    ],
                },
                {
                    "id": "revision-adjusted",
                    "parent": "revision-initial",
                    "source_id": "source-curator-note",
                    "assertions": [
                        {
                            "id": "assertion-current-sort",
                            "key": "sort_mode",
                            "value": "by-cultivar",
                            "supersedes": ["assertion-initial-sort"],
                            "source_id": "source-curator-note",
                        }
                    ],
                },
            ],
        }

        code, result, _ = self.invoke(canonical_bytes(snapshot()), canonical_bytes(ledger))

        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["reason"]), ("STALE", "setting_value_changed"))
        drift = result["key_drift"][0]
        self.assertEqual(drift["current_status"], "stable")
        self.assertEqual(drift["current_values"], [{"value": "by-cultivar", "value_type": "string"}])
        self.assertEqual(drift["assertion_ids"], ["assertion-current-sort"])
        self.assertEqual(drift["revision_ids"], ["revision-adjusted"])
        self.assertEqual(drift["source_ids"], ["source-curator-note"])
        self.assertEqual(
            drift["current_heads"],
            [
                {
                    "head_id": "revision-adjusted",
                    "assertion_ids": ["assertion-current-sort"],
                    "withdrawal_count": 0,
                    "live_count": 1,
                }
            ],
        )

    def test_withdrawal_is_blocked_not_stale_and_retains_withdrawal_detail(self):
        ledger = {
            "scope": "fictional-greenhouse-console",
            "required_keys": ["sort_mode"],
            "revisions": [
                {
                    "id": "revision-initial",
                    "assertions": [
                        {"id": "assertion-initial-sort", "key": "sort_mode", "value": "by-bed"}
                    ],
                },
                {
                    "id": "revision-withdrawal",
                    "parent": "revision-initial",
                    "source_id": "source-curator-note",
                    "withdrawals": [
                        {
                            "id": "assertion-withdraw-sort",
                            "target": "assertion-initial-sort",
                            "key": "sort_mode",
                            "source_id": "source-curator-note",
                            "reason": "the displayed order needs review",
                        }
                    ],
                },
            ],
        }

        code, result, _ = self.invoke(canonical_bytes(snapshot()), canonical_bytes(ledger))

        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["reason"], result["resolver_status"]), ("BLOCKED", "ledger_not_ready", "REVIEW"))
        drift = result["key_drift"][0]
        self.assertEqual(drift["current_status"], "withdrawn")
        self.assertEqual(drift["current_values"], [])
        self.assertEqual(drift["withdrawals"][0]["assertion_id"], "assertion-withdraw-sort")
        self.assertEqual(drift["withdrawals"][0]["revision_id"], "revision-withdrawal")
        self.assertEqual(drift["withdrawals"][0]["source_id"], "source-curator-note")
        self.assertEqual(drift["source_ids"], ["source-curator-note"])

    def test_conflicting_heads_are_blocked_and_both_current_heads_are_reported(self):
        ledger = {
            "scope": "fictional-greenhouse-console",
            "required_keys": ["sort_mode"],
            "head_blocks": [
                {
                    "id": "head-bench-north",
                    "revision_id": "revision-north",
                    "source_id": "source-north-label",
                    "assertions": [
                        {
                            "id": "assertion-north-sort",
                            "key": "sort_mode",
                            "value": "by-bed",
                            "source_id": "source-north-label",
                        }
                    ],
                },
                {
                    "id": "head-bench-south",
                    "revision_id": "revision-south",
                    "source_id": "source-south-label",
                    "assertions": [
                        {
                            "id": "assertion-south-sort",
                            "key": "sort_mode",
                            "value": "by-cultivar",
                            "source_id": "source-south-label",
                        }
                    ],
                },
            ],
        }

        code, result, _ = self.invoke(canonical_bytes(snapshot()), canonical_bytes(ledger))

        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["resolver_status"]), ("BLOCKED", "REVIEW"))
        drift = result["key_drift"][0]
        self.assertEqual(drift["current_status"], "conflicting")
        self.assertEqual(
            drift["current_values"],
            [
                {"value": "by-bed", "value_type": "string"},
                {"value": "by-cultivar", "value_type": "string"},
            ],
        )
        self.assertEqual(drift["assertion_ids"], ["assertion-north-sort", "assertion-south-sort"])
        self.assertEqual(drift["source_ids"], ["source-north-label", "source-south-label"])
        self.assertEqual(
            [item["head_id"] for item in drift["current_heads"]],
            ["head-bench-north", "head-bench-south"],
        )

    def test_missing_predecessor_is_invalid_not_an_inferred_ready_head(self):
        ledger = {
            "scope": "fictional-greenhouse-console",
            "required_keys": ["sort_mode"],
            "revisions": [
                {
                    "id": "revision-current",
                    "parent": "revision-not-supplied",
                    "assertions": [
                        {"id": "assertion-sort-mode", "key": "sort_mode", "value": "by-bed"}
                    ],
                }
            ],
        }

        code, result, _ = self.invoke(canonical_bytes(snapshot()), canonical_bytes(ledger))

        self.assertEqual(code, 2)
        self.assertEqual((result["status"], result["reason"], result["resolver_status"]), ("INVALID", "ledger_invalid", "INVALID"))

    def test_boolean_snapshot_value_does_not_equal_integer_ledger_value(self):
        code, result, _ = self.invoke(
            canonical_bytes(snapshot(True, key="alarm_enabled")),
            canonical_bytes(one_setting_ledger(1, key="alarm_enabled")),
        )

        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["reason"]), ("STALE", "setting_value_changed"))
        drift = result["key_drift"][0]
        self.assertEqual(drift["snapshot_value_type"], "boolean")
        self.assertEqual(drift["current_values"], [{"value": 1, "value_type": "integer"}])
        self.assertFalse(drift["value_matches"])

    def test_scope_and_exact_key_set_changes_are_stale_only_for_ready_ledger(self):
        changed_scope = snapshot(scope="fictional-greenhouse-tablet")
        code, result, _ = self.invoke(canonical_bytes(changed_scope), canonical_bytes(one_setting_ledger()))
        self.assertEqual((code, result["status"], result["reason"]), (1, "STALE", "scope_changed"))
        extra_key_snapshot = {"scope": "fictional-greenhouse-console", "settings": {"sort_mode": "by-bed", "extra_filter": "orchid"}}
        code, result, _ = self.invoke(canonical_bytes(extra_key_snapshot), canonical_bytes(one_setting_ledger()))
        self.assertEqual((code, result["status"], result["reason"]), (1, "STALE", "required_keys_changed"))
        self.assertFalse(result["settings_keys_match"])

    def test_malformed_json_is_invalid_with_actual_complete_input_metadata(self):
        ledger_raw = canonical_bytes(one_setting_ledger())
        code, result, _ = self.invoke(b"{broken", ledger_raw)

        self.assertEqual(code, 2)
        self.assertEqual((result["status"], result["reason"]), ("INVALID", "snapshot_json_invalid"))
        self.assertEqual(result["inputs"]["snapshot"]["bytes"], len(b"{broken"))
        self.assertEqual(result["inputs"]["snapshot"]["sha256"], hashlib.sha256(b"{broken").hexdigest())
        self.assertTrue(result["inputs"]["snapshot"]["complete"])

    def test_contract_invalid_snapshot_is_invalid_after_current_ledger_resolution(self):
        invalid_snapshot = {"scope": "fictional-greenhouse-console", "settings": {" ": "by-bed"}}

        code, result, _ = self.invoke(canonical_bytes(invalid_snapshot), canonical_bytes(one_setting_ledger()))

        self.assertEqual(code, 2)
        self.assertEqual((result["status"], result["reason"], result["resolver_status"]), ("INVALID", "snapshot_contract_invalid", "READY"))
        self.assertEqual(result["validation_errors"], ["settings_key"])

    def test_oversize_snapshot_and_ledger_are_invalid_with_incomplete_markers(self):
        valid_snapshot = canonical_bytes(snapshot())
        valid_ledger = canonical_bytes(one_setting_ledger())
        oversized = b" " * (INPUT_LIMIT + 1)
        for snapshot_raw, ledger_raw, field in (
            (oversized, valid_ledger, "snapshot"),
            (valid_snapshot, oversized, "ledger"),
        ):
            with self.subTest(field=field):
                code, result, _ = self.invoke(snapshot_raw, ledger_raw)
                self.assertEqual((code, result["status"], result["reason"]), (2, "INVALID", "input_limit"))
                self.assertEqual(result["inputs"][field]["bytes"], INPUT_LIMIT + 1)
                self.assertIsNone(result["inputs"][field]["sha256"])
                self.assertFalse(result["inputs"][field]["complete"])

    def test_result_overflow_uses_bounded_blocked_detail_fallback(self):
        large_snapshot = {
            "scope": "fictional-greenhouse-console",
            "settings": {f"archived_filter_{index}": "x" * 2100 for index in range(8)},
        }

        code, result, output = self.invoke(
            canonical_bytes(large_snapshot),
            canonical_bytes(one_setting_ledger()),
        )

        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["reason"]), ("BLOCKED", "detail_required"))
        self.assertFalse(result["details_complete"])
        self.assertTrue(all(item["details_complete"] is False for item in result["key_drift"]))
        self.assertLessEqual(len(output), RESULT_LIMIT + 1)

    def test_output_is_byte_deterministic(self):
        snapshot_raw = canonical_bytes(snapshot())
        ledger_raw = canonical_bytes(one_setting_ledger())
        first_code, first, first_output = self.invoke(snapshot_raw, ledger_raw)
        second_code, second, second_output = self.invoke(snapshot_raw, ledger_raw)

        self.assertEqual(first_code, second_code)
        self.assertEqual(first, second)
        self.assertEqual(first_output, second_output)


if __name__ == "__main__":
    unittest.main()
