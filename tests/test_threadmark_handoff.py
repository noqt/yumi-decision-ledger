"""Focused synthetic checks for the bounded Threadmark handoff exporter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "threadmark_handoff.py"


def load_module():
    spec = importlib.util.spec_from_file_location("threadmark_handoff", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("handoff exporter could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state(
    key: str,
    status: str,
    *,
    values=None,
    assertion_ids=None,
    revision_ids=None,
    source_ids=None,
    withdrawals=None,
):
    return {
        "key": key,
        "required": True,
        "status": status,
        "values": values or [],
        "heads": [
            {
                "head_id": "head-1",
                "assertion_ids": assertion_ids or [],
                "live_count": len(assertion_ids or []),
                "withdrawal_count": len(withdrawals or []),
            }
        ],
        "assertion_ids": assertion_ids or [],
        "revision_ids": revision_ids or [],
        "source_ids": source_ids or [],
        "withdrawals": withdrawals or [],
    }


def receipt(key_states, *, status="READY", required_keys=None):
    required = required_keys or sorted(key_states)
    return {
        "resolver": "yumi-continuity",
        "schema": "yumi.continuity.threadmark.v1.1",
        "mode": "threadmark",
        "status": status,
        "decision": status,
        "ready": status == "READY",
        "scope": "synthetic-handoff",
        "input_sha256": "a" * 64,
        "input_bytes": 321,
        "required_keys": required,
        "heads": ["head-1"],
        "key_states": key_states,
        "assertion_ids": sorted(
            {item for value in key_states.values() for item in value["assertion_ids"]}
        ),
        "revision_ids": sorted(
            {item for value in key_states.values() for item in value["revision_ids"]}
        ),
        "source_ids": sorted(
            {item for value in key_states.values() for item in value["source_ids"]}
        ),
        "blockers": [],
        "capability": {
            "producer": "Yumi",
            "capability": "continuity-threadmark",
            "version": "1.1",
            "doc_url": "https://github.com/noqt/noqt/tree/main/yumi/continuity/v1.1",
            "inert": True,
        },
    }


class HandoffCardCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def export(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return self.module.export_handoff(value, raw_bytes=raw), raw

    def test_conflict_retains_every_typed_value_and_ref(self):
        value = receipt(
            {
                "decision": state(
                    "decision",
                    "conflicting",
                    values=[
                        {"value": "proceed", "value_type": "string"},
                        {"value": "hold", "value_type": "string"},
                    ],
                    assertion_ids=["a-hold", "a-proceed"],
                    revision_ids=["r-1", "r-2"],
                    source_ids=["review-a", "review-b"],
                )
            },
            status="REVIEW",
        )
        card, raw = self.export(value)
        self.assertEqual(card["status"], "REVIEW")
        self.assertFalse(card["ready"])
        retained = card["key_states"]["decision"]
        self.assertEqual(retained["status"], "conflicting")
        self.assertEqual(
            retained["values"],
            [
                {"value": "hold", "value_type": "string"},
                {"value": "proceed", "value_type": "string"},
            ],
        )
        self.assertEqual(retained["assertion_ids"], ["a-hold", "a-proceed"])
        self.assertEqual(card["receipt_sha256"], hashlib.sha256(raw).hexdigest())

    def test_missing_required_key_is_explicit_and_not_ready(self):
        value = receipt(
            {"decision": state("decision", "stable", values=[{"value": "go", "value_type": "string"}])},
            required_keys=["decision", "target"],
        )
        card, _ = self.export(value)
        self.assertEqual(card["status"], "REVIEW")
        self.assertFalse(card["ready"])
        self.assertEqual(card["key_states"]["target"]["status"], "missing")
        self.assertTrue(any(item.get("key") == "target" for item in card["blockers"]))

    def test_withdrawn_state_and_withdrawal_reference_are_retained(self):
        withdrawal = {
            "target": "a-decision",
            "key": "decision",
            "assertion_id": "w-1",
            "revision_id": "r-2",
            "source_id": "operator-note",
        }
        value = receipt(
            {"decision": state("decision", "withdrawn", assertion_ids=["a-decision"], withdrawals=[withdrawal])},
            status="REVIEW",
        )
        card, _ = self.export(value)
        retained = card["key_states"]["decision"]
        self.assertEqual(card["status"], "REVIEW")
        self.assertEqual(retained["status"], "withdrawn")
        self.assertEqual(retained["withdrawals"], [withdrawal])

    def test_typed_values_are_not_coerced(self):
        value = receipt(
            {
                "flag": state(
                    "flag",
                    "conflicting",
                    values=[
                        {"value": True, "value_type": "boolean"},
                        {"value": 1, "value_type": "integer"},
                        {"value": 1.0, "value_type": "number"},
                    ],
                    assertion_ids=["a-bool", "a-int", "a-num"],
                )
            },
            status="REVIEW",
        )
        card, _ = self.export(value)
        self.assertEqual(
            card["key_states"]["flag"]["values"],
            [
                {"value": 1, "value_type": "integer"},
                {"value": 1.0, "value_type": "number"},
                {"value": True, "value_type": "boolean"},
            ],
        )

    def test_output_bound_returns_detail_required_without_truncation(self):
        value = receipt(
            {
                "decision": state(
                    "decision",
                    "conflicting",
                    values=[
                        {"value": "x" * 5000, "value_type": "string"},
                        {"value": "y" * 5000, "value_type": "string"},
                    ],
                )
            },
            status="REVIEW",
        )
        card, _ = self.export(value)
        encoded = self.module.canonical_bytes(card)
        self.assertEqual(card["status"], "DETAIL_REQUIRED")
        self.assertEqual(card["decision"], "DETAIL_REQUIRED")
        self.assertFalse(card["ready"])
        self.assertTrue(card["detail_required"])
        self.assertFalse(card["details_retained"])
        self.assertLessEqual(len(encoded), self.module.MAX_OUTPUT_BYTES)
        self.assertNotIn("key_states", card)

    def test_input_bound_is_invalid_and_digest_is_retained(self):
        raw = b"{" + b"x" * self.module.MAX_INPUT_BYTES + b"}"
        card = self.module.export_handoff({}, raw_bytes=raw)
        self.assertEqual(card["status"], "INVALID")
        self.assertFalse(card["ready"])
        self.assertEqual(card["receipt_bytes"], len(raw))
        self.assertEqual(card["receipt_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(card["blockers"], [{"code": "input_limit", "required": True}])
        self.assertLessEqual(len(self.module.canonical_bytes(card)), self.module.MAX_OUTPUT_BYTES)

    def test_measured_receipt_bytes_and_digest_are_exact(self):
        value = receipt(
            {
                "decision": state(
                    "decision",
                    "stable",
                    values=[{"value": "go", "value_type": "string"}],
                    assertion_ids=["a-1"],
                    revision_ids=["r-1"],
                    source_ids=["source-1"],
                )
            }
        )
        raw = b'{"scope":"synthetic-handoff","required_keys":["decision"]}'
        # The object is intentionally different from ``raw``: the exporter
        # must report the bytes actually supplied by the caller, not a
        # reconstructed ledger or canonicalized substitute.
        card = self.module.export_handoff(value, raw_bytes=raw)
        self.assertEqual(card["receipt_bytes"], len(raw))
        self.assertEqual(card["receipt_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(card["input_sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
