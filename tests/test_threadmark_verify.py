"""Focused actual-CLI checks for the bounded Threadmark freshness checker."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "threadmark_verify.py"
RESOLVER_SCRIPT = ROOT / "yumi_continuity.py"
HANDOFF_SCRIPT = ROOT / "threadmark_handoff.py"


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"module could not be loaded: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ledger(decision="continue"):
    return {
        "scope": "synthetic-handoff",
        "required_keys": ["task", "decision"],
        "head_blocks": [
            {
                "id": "head-main",
                "revision_id": "revision-main",
                "assertions": [
                    {"id": "assert-task", "key": "task", "value": "review"},
                    {"id": "assert-decision", "key": "decision", "value": decision},
                ],
            }
        ],
    }


def ready_card(value, raw, resolver, handoff, receipt_raw=None):
    receipt = resolver.threadmark_ledger(value, raw_bytes=raw)
    if receipt.get("status") != "READY":
        raise AssertionError(f"test fixture must resolve READY, got {receipt.get('status')}")
    card = handoff.export_handoff(receipt, raw_bytes=receipt_raw or canonical_bytes(receipt))
    if card.get("status") != "READY":
        raise AssertionError(f"test card must be READY, got {card.get('status')}")
    return canonical_bytes(card)


class ThreadmarkVerifyCliCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = load_module("threadmark_verify_test_resolver", RESOLVER_SCRIPT)
        cls.handoff = load_module("threadmark_verify_test_handoff", HANDOFF_SCRIPT)

    def setUp(self):
        temp_root = os.environ.get("THREADMARK_VERIFY_TEST_TMP", str(ROOT))
        self.temp = tempfile.TemporaryDirectory(prefix="threadmark-verify-test-", dir=temp_root)
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, card_raw, ledger_raw):
        card_path = self.directory / "card.json"
        ledger_path = self.directory / "ledger.json"
        card_path.write_bytes(card_raw)
        ledger_path.write_bytes(ledger_raw)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                str(SCRIPT),
                "check",
                "--card",
                str(card_path),
                "--ledger",
                str(ledger_path),
            ],
            # Running from a different directory also verifies the -I sibling
            # resolver load is anchored to this script, not the working dir.
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertLessEqual(len(completed.stdout.encode("utf-8")), 16 * 1024)
        return completed.returncode, result

    def test_unchanged_snapshot_reports_digest_links_and_per_key_match(self):
        value = ledger()
        raw = canonical_bytes(value)
        receipt = self.resolver.threadmark_ledger(value, raw_bytes=raw)
        whitespace_transport = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
        card_raw = ready_card(value, raw, self.resolver, self.handoff, whitespace_transport)

        code, result = self.invoke(card_raw, raw)

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertEqual(result["reason"], "required_key_snapshot_matches")
        self.assertEqual(result["ledger"]["sha256"], self.resolver._tm_sha256(raw))
        self.assertTrue(result["provenance_links"]["claimed_input_digest_matches_supplied_ledger"])
        self.assertFalse(result["provenance_links"]["claims_authenticated"])
        self.assertEqual(result["capability"]["capability"], "handoff-freshness-check")
        self.assertEqual(result["capability"]["version"], "1.2")
        self.assertEqual(
            result["capability"]["doc_url"],
            "https://github.com/noqt/yumi-decision-ledger",
        )
        self.assertEqual(
            result["capability"]["base_capability"]["doc_url"],
            "https://github.com/noqt/noqt/tree/main/yumi/continuity/v1.1",
        )
        self.assertEqual({item["key"] for item in result["key_drift"]}, {"task", "decision"})
        self.assertTrue(all(item["matches"] for item in result["key_drift"]))

    def test_non_required_global_context_refs_do_not_make_required_snapshot_stale(self):
        value = ledger()
        raw = canonical_bytes(value)
        card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        card["assertion_ids"].append("context-only-assertion")
        card["revision_ids"].append("context-only-revision")
        card["source_ids"].append("context-only-source")

        code, result = self.invoke(canonical_bytes(card), raw)

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertTrue(all(item["matches"] for item in result["key_drift"]))

    def test_forged_but_well_shaped_declared_heads_are_stale(self):
        value = ledger()
        raw = canonical_bytes(value)
        card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        card["heads"] = ["head-forged"]
        for state in card["key_states"].values():
            for observation in state["heads"]:
                observation["head_id"] = "head-forged"

        code, result = self.invoke(canonical_bytes(card), raw)

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "declared_heads_changed")
        self.assertFalse(result["heads_match"])

    def test_ready_card_head_observations_must_match_declared_set(self):
        value = ledger()
        raw = canonical_bytes(value)
        card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        card["heads"] = ["head-forged"]

        code, result = self.invoke(canonical_bytes(card), raw)

        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(
            any("heads_declared_set" in error for error in result["validation_errors"])
        )

    def test_top_level_head_shape_bounds_and_uniqueness_are_validated(self):
        value = ledger()
        raw = canonical_bytes(value)
        base_card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        malformed_heads = (
            "head-main",
            [],
            None,
            [["not-an-id"]],
            ["head-main", "head-main"],
            [f"head-{index}" for index in range(17)],
        )

        for malformed in malformed_heads:
            with self.subTest(heads=malformed):
                card = dict(base_card)
                card["heads"] = malformed

                code, result = self.invoke(canonical_bytes(card), raw)

                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "INVALID")
                self.assertIn("heads", result["validation_errors"])

    def test_top_level_global_reference_arrays_are_shape_validated(self):
        value = ledger()
        raw = canonical_bytes(value)
        base_card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        malformed_refs = {
            "assertion_ids": "assert-task",
            "revision_ids": [None],
            "source_ids": [["nested-source"]],
        }

        for field, malformed in malformed_refs.items():
            with self.subTest(field=field):
                card = dict(base_card)
                card[field] = malformed

                code, result = self.invoke(canonical_bytes(card), raw)

                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "INVALID")
                self.assertIn(field, result["validation_errors"])

    def test_new_required_key_is_stale_even_when_old_keys_are_unchanged(self):
        original = ledger()
        old_raw = canonical_bytes(original)
        card_raw = ready_card(original, old_raw, self.resolver, self.handoff)
        expanded = ledger()
        expanded["required_keys"].append("target")
        expanded["head_blocks"][0]["assertions"].append(
            {"id": "assert-target", "key": "target", "value": "unit-test"}
        )

        code, result = self.invoke(card_raw, canonical_bytes(expanded))

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "required_keys_changed")
        self.assertFalse(result["required_keys_match"])
        drift = {item["key"]: item for item in result["key_drift"]}
        self.assertTrue(drift["decision"]["matches"])
        self.assertTrue(drift["task"]["matches"])
        self.assertEqual(drift["target"]["previous_status"], "not_in_card")
        self.assertFalse(drift["target"]["matches"])

    def test_explicit_value_replacement_is_stale_and_localized(self):
        original = ledger("continue")
        old_raw = canonical_bytes(original)
        card_raw = ready_card(original, old_raw, self.resolver, self.handoff)
        replacement_raw = canonical_bytes(ledger("pause"))

        code, result = self.invoke(card_raw, replacement_raw)

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "STALE", result)
        self.assertEqual(result["reason"], "key_state_changed")
        drift = {item["key"]: item for item in result["key_drift"]}
        self.assertTrue(drift["task"]["matches"])
        self.assertFalse(drift["decision"]["matches"])
        self.assertFalse(drift["decision"]["values_match"])
        self.assertFalse(result["provenance_links"]["claimed_input_digest_matches_supplied_ledger"])

    def test_withdrawal_blocks_resume_and_is_reported_per_key(self):
        original = ledger()
        original_raw = canonical_bytes(original)
        card_raw = ready_card(original, original_raw, self.resolver, self.handoff)
        withdrawn = {
            "scope": "synthetic-handoff",
            "required_keys": ["task", "decision"],
            "revisions": [
                {
                    "id": "revision-1",
                    "assertions": [
                        {"id": "assert-task", "key": "task", "value": "review"},
                        {"id": "assert-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "revision-2",
                    "parent": "revision-1",
                    "withdrawals": [{"target": "assert-decision", "reason": "synthetic withdrawal"}],
                },
            ],
        }

        code, result = self.invoke(card_raw, canonical_bytes(withdrawn))

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "ledger_not_ready")
        drift = {item["key"]: item for item in result["key_drift"]}
        self.assertEqual(drift["decision"]["current_status"], "withdrawn")
        self.assertFalse(drift["decision"]["matches"])

    def test_new_conflicting_head_blocks_even_when_one_head_matches_card(self):
        original = ledger()
        card_raw = ready_card(original, canonical_bytes(original), self.resolver, self.handoff)
        conflicting = {
            "scope": "synthetic-handoff",
            "required_keys": ["task", "decision"],
            "head_blocks": [
                {
                    "id": "head-left",
                    "revision_id": "revision-left",
                    "assertions": [
                        {"id": "left-task", "key": "task", "value": "review"},
                        {"id": "left-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "head-right",
                    "revision_id": "revision-right",
                    "assertions": [
                        {"id": "right-task", "key": "task", "value": "review"},
                        {"id": "right-decision", "key": "decision", "value": "pause"},
                    ],
                },
            ],
        }

        code, result = self.invoke(card_raw, canonical_bytes(conflicting))

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "BLOCKED")
        drift = {item["key"]: item for item in result["key_drift"]}
        self.assertEqual(drift["decision"]["current_status"], "conflicting")
        self.assertFalse(drift["decision"]["values_match"])

    def test_matching_digest_cannot_hide_wrong_typed_card_value(self):
        value = {
            "scope": "typed-handoff",
            "required_keys": ["attempt"],
            "head_blocks": [
                {
                    "id": "head-typed",
                    "revision_id": "revision-typed",
                    "assertions": [{"id": "assert-attempt", "key": "attempt", "value": 1}],
                }
            ],
        }
        raw = canonical_bytes(value)
        original_card = ready_card(value, raw, self.resolver, self.handoff)
        # Keep the exact matching input digest but forge a well-typed boolean
        # or float where the freshly resolved ledger contains integer 1.
        for replacement, type_name in ((True, "boolean"), (1.0, "number")):
            with self.subTest(replacement=type_name):
                card = json.loads(original_card)
                card["key_states"]["attempt"]["values"] = [
                    {"value": replacement, "value_type": type_name}
                ]

                code, result = self.invoke(canonical_bytes(card), raw)

                self.assertEqual(code, 1)
                self.assertEqual(result["status"], "STALE", result)
                self.assertTrue(result["provenance_links"]["claimed_input_digest_matches_supplied_ledger"])
                drift = result["key_drift"][0]
                self.assertEqual(drift["key"], "attempt")
                self.assertFalse(drift["values_match"])
                self.assertFalse(drift["matches"])

    def test_wrong_value_type_label_is_invalid(self):
        value = {
            "scope": "typed-handoff",
            "required_keys": ["attempt"],
            "head_blocks": [
                {
                    "id": "head-typed",
                    "revision_id": "revision-typed",
                    "assertions": [{"id": "assert-attempt", "key": "attempt", "value": 1}],
                }
            ],
        }
        raw = canonical_bytes(value)
        card = json.loads(ready_card(value, raw, self.resolver, self.handoff))
        card["key_states"]["attempt"]["values"][0]["value_type"] = "boolean"

        code, result = self.invoke(canonical_bytes(card), raw)

        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "card_contract_invalid")
        self.assertTrue(any("value_type" in error for error in result["validation_errors"]))

    def test_invalid_json_and_both_input_bounds_fail_closed(self):
        value = ledger()
        raw = canonical_bytes(value)
        card_raw = ready_card(value, raw, self.resolver, self.handoff)

        code, result = self.invoke(b"not-json", raw)
        self.assertEqual(code, 2)
        self.assertEqual((result["status"], result["reason"]), ("INVALID", "card_json_invalid"))

        code, result = self.invoke(b"x" * (4 * 1024 + 1), raw)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "input_limit")
        self.assertEqual(result["limits"]["overflow"], "card")
        self.assertIsNone(result["card"]["sha256"])

        code, result = self.invoke(card_raw, b" " * (64 * 1024 + 1))
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "input_limit")
        self.assertEqual(result["limits"]["overflow"], "ledger")
        self.assertIsNone(result["ledger"]["sha256"])

    def test_resolver_bounded_detail_fallback_never_compares_as_empty_equal(self):
        original = {
            "scope": "bounded-handoff",
            "required_keys": ["decision"],
            "head_blocks": [
                {
                    "id": "head-original",
                    "revision_id": "revision-original",
                    "assertions": [{"id": "assert-original", "key": "decision", "value": "continue"}],
                }
            ],
        }
        original_raw = canonical_bytes(original)
        card_raw = ready_card(original, original_raw, self.resolver, self.handoff)
        large = {
            "scope": "bounded-handoff",
            "required_keys": ["decision"],
            "head_blocks": [
                {
                    "id": f"head-{index}",
                    "revision_id": f"revision-{index}",
                    "assertions": [
                        {"id": f"assert-{index}", "key": "decision", "value": "x" * 1000}
                    ],
                }
                for index in range(16)
            ],
        }
        large_raw = canonical_bytes(large)
        self.assertLess(len(large_raw), 64 * 1024)
        self.assertEqual(
            self.resolver.threadmark_ledger(large, raw_bytes=large_raw).get("status"),
            "DETAIL_REQUIRED",
        )

        code, result = self.invoke(card_raw, large_raw)

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "ledger_detail_unavailable")
        self.assertEqual(result["ledger"]["resolver_status"], "DETAIL_REQUIRED")
        self.assertEqual(result["key_drift"], [])


if __name__ == "__main__":
    unittest.main()
