"""Frozen contract cases for the standalone Yumi continuity resolver.

These tests deliberately use only synthetic ledgers.  They are written before
the implementation so the resolver contract is explicit and repeatable.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "yumi_continuity.py"


def load_module():
    spec = importlib.util.spec_from_file_location("yumi_continuity", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("resolver module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ledger(revisions, *, prior_heads=None, heads=None):
    value = {
        "schema": "yumi.continuity.ledger.v1",
        "scope": "synthetic-session",
        "revisions": revisions,
    }
    if prior_heads is not None:
        value["prior_heads"] = prior_heads
    if heads is not None:
        value["heads"] = heads
    return value


def revision(identifier, parent=None, *, sequence=None, assertions=None, **extra):
    result = {"id": identifier, "parent": parent, "assertions": assertions or []}
    if sequence is not None:
        result["sequence"] = sequence
    result.update(extra)
    return result


def assertion(key, value=None, *, identifier=None, state="asserted", **extra):
    result = {"key": key, "state": state}
    if value is not None:
        result["value"] = value
    if identifier is not None:
        result["id"] = identifier
    result.update(extra)
    return result


class FrozenContinuityCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = load_module()

    def resolve(self, value):
        return self.resolver.resolve_ledger(value)

    def test_case_01_control_assertion_and_unverified_source(self):
        result = self.resolve(
            ledger(
                [
                    revision(
                        "r1",
                        sequence=1,
                        assertions=[
                            assertion(
                                "status",
                                "ready",
                                identifier="a1",
                                source_label="operator",
                                source_ref="opaque://note/1",
                            )
                        ],
                    )
                ]
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["current_assertions"][0]["key"], "status")
        self.assertEqual(result["current_assertions"][0]["provenance"]["verification"], "unverified")
        self.assertEqual(result["current_assertions"][0]["provenance"]["source_ref"], "opaque://note/1")
        self.assertFalse(result["ambiguity"]["is_ambiguous"])

    def test_case_02_revision_supersession_chain(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("mode", "draft", identifier="a1")]),
                    revision("r2", "r1", sequence=2, assertions=[assertion("mode", "ready", identifier="a2")]),
                    revision("r3", "r2", sequence=3, assertions=[assertion("mode", "sent", identifier="a3")]),
                ]
            )
        )
        self.assertEqual(result["heads"], ["r3"])
        self.assertEqual(result["current_assertions"][0]["value"], "sent")
        self.assertEqual({item["assertion_id"] for item in result["superseded_assertions"]}, {"a1", "a2"})
        self.assertEqual(result["revision_chains"]["r3"], ["r1", "r2", "r3"])

    def test_case_03_assertion_retraction(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("flag", True, identifier="a1")]),
                    revision("r2", "r1", sequence=2, assertions=[assertion("flag", state="retracted", target="a1")]),
                ]
            )
        )
        self.assertEqual(result["current_assertions"], [])
        self.assertEqual(result["retractions"][0]["target"], "a1")
        self.assertEqual(result["retractions"][0]["status"], "retracted")

    def test_case_04_revision_retraction_chain(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("flag", True, identifier="a1")]),
                    revision("r2", "r1", sequence=2, retracts=["a1"]),
                    revision("r3", "r2", sequence=3, assertions=[assertion("flag", False, identifier="a3")]),
                    revision("r4", "r3", sequence=4, assertions=[assertion("flag", state="retracted", target="a1")]),
                ]
            )
        )
        self.assertEqual(result["current_assertions"][0]["value"], False)
        self.assertEqual([item["target"] for item in result["retractions"]], ["a1", "a1"])
        self.assertEqual([item["revision_id"] for item in result["retractions"]], ["r2", "r4"])
        self.assertEqual([item["effective"] for item in result["retractions"]], [True, False])

    def test_case_05_fork_is_explicitly_ambiguous(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("status", "base", identifier="a1")]),
                    revision("r2a", "r1", sequence=2, assertions=[assertion("status", "left", identifier="a2")]),
                    revision("r2b", "r1", sequence=3, assertions=[assertion("status", "right", identifier="a3")]),
                ]
            )
        )
        self.assertTrue(result["ambiguity"]["is_ambiguous"])
        self.assertEqual(result["heads"], ["r2a", "r2b"])
        self.assertEqual(result["forks"][0]["parent"], "r1")
        self.assertEqual(result["current_assertions"], [])
        self.assertEqual(len(result["head_results"]), 2)

        converged = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("status", "base", identifier="a1")]),
                    revision("r2", "r1", sequence=2, assertions=[assertion("status", "left", identifier="a2")]),
                    revision("r3", "r1", sequence=3, assertions=[assertion("status", "right", identifier="a3")]),
                    revision("r4", "r2", sequence=4, supersedes=["r3"], assertions=[assertion("status", "merged", identifier="a4")]),
                ]
            )
        )
        self.assertEqual(converged["status"], "invalid")
        self.assertTrue(any("convergent_fork_requires_explicit_merge" in item for item in converged["diagnostics"]["errors"]))

    def test_case_06_missing_predecessor_is_failed_closed(self):
        result = self.resolve(
            ledger([revision("r2", "does-not-exist", sequence=2, assertions=[assertion("x", 1)])])
        )
        self.assertEqual(result["status"], "invalid")
        self.assertIn("does-not-exist", result["diagnostics"]["missing_references"])
        self.assertEqual(result["current_assertions"], [])

    def test_case_07_parent_cycle_is_explicit(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", "r2", sequence=1),
                    revision("r2", "r1", sequence=2),
                ]
            )
        )
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(result["diagnostics"]["cycles"])

    def test_case_08_supersession_cycle_is_explicit(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, supersedes=["r2"]),
                    revision("r2", sequence=2, supersedes=["r1"]),
                ]
            )
        )
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(result["diagnostics"]["cycles"])

    def test_case_09_duplicate_replay_is_deduplicated(self):
        r1 = revision("r1", sequence=1, assertions=[assertion("x", 1, identifier="a1")])
        result = self.resolve(ledger([r1, json.loads(json.dumps(r1))]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostics"]["duplicate_revisions"], ["r1"])
        self.assertEqual(len(result["current_assertions"]), 1)

    def test_case_10_shuffle_stability_and_fresh_cli_equality(self):
        revisions = [
            revision("r2", "r1", sequence=2, assertions=[assertion("x", 2, identifier="a2")]),
            revision("r1", sequence=1, assertions=[assertion("x", 1, identifier="a1")]),
        ]
        first = self.resolve(ledger(revisions))
        second = self.resolve(ledger(list(reversed(revisions))))
        self.assertEqual(first, second)
        payload = json.dumps(ledger(revisions), sort_keys=True)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        outputs = []
        for _ in range(2):
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-S", str(SCRIPT), "resolve", "--input", "-"],
                input=payload,
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            self.assertEqual(completed.stderr, "")
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_case_11_delta_from_supplied_prior_head(self):
        result = self.resolve(
            ledger(
                [
                    revision("r1", sequence=1, assertions=[assertion("x", 1, identifier="a1")]),
                    revision("r2", "r1", sequence=2, assertions=[assertion("x", 2, identifier="a2"), assertion("y", "new", identifier="a3")]),
                ],
                prior_heads=["r1"],
            )
        )
        self.assertEqual(result["delta"]["from_prior_heads"], ["r1"])
        self.assertEqual(result["delta"]["changed"][0]["key"], "x")
        self.assertEqual(result["delta"]["added"][0]["key"], "y")
        self.assertEqual(result["delta"]["removed"], [])

    def test_case_12_control_bounds_and_malformed_input(self):
        malformed = self.resolve({"scope": "synthetic-session", "revisions": "not-a-list"})
        self.assertEqual(malformed["status"], "invalid")
        too_many = ledger([revision(f"r{i}", sequence=i) for i in range(129)])
        bounded = self.resolve(too_many)
        self.assertEqual(bounded["status"], "invalid")
        self.assertIn("revision_count", bounded["diagnostics"]["limits"])
        oversized = self.resolve({"scope": "synthetic-session", "revisions": [], "padding": "x" * 70000})
        self.assertEqual(oversized["status"], "invalid")
        self.assertIn("input_bytes", oversized["diagnostics"]["limits"])


class ThreadmarkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.threadmark = staticmethod(cls.module.threadmark_ledger)

    def resolve_threadmark(self, value):
        return self.threadmark(value)

    @staticmethod
    def base(*, required_keys=None, heads=None, revisions=None, **extra):
        result = {
            "schema": "yumi.threadmark.ledger.v1.1",
            "scope": "synthetic-cross-domain-handoff",
            "required_keys": required_keys or ["task", "decision"],
            "revisions": revisions or [
                {
                    "id": "r1",
                    "sequence": 1,
                    "source_id": "source-a",
                    "assertions": [
                        {"id": "a-task", "key": "task", "value": "review"},
                        {"id": "a-decision", "key": "decision", "value": "continue"},
                    ],
                }
            ],
        }
        if heads is not None:
            result["heads"] = heads
        result.update(extra)
        return result

    def test_threadmark_ready_requires_all_required_keys_and_exposes_bindings(self):
        result = self.resolve_threadmark(self.base())
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["ready"])
        self.assertEqual(result["heads"], ["r1"])
        self.assertEqual(result["required_keys"], ["decision", "task"])
        self.assertEqual(result["key_states"]["task"]["status"], "stable")
        self.assertEqual(result["key_states"]["decision"]["status"], "stable")
        self.assertEqual(result["assertion_ids"], ["a-decision", "a-task"])
        self.assertEqual(result["revision_ids"], ["r1"])
        self.assertEqual(result["source_ids"], ["source-a"])
        self.assertEqual(len(result["input_sha256"]), 64)
        self.assertTrue(result["capability"]["inert"])
        self.assertEqual(result["capability"]["producer"], "Yumi")

    def test_threadmark_considers_four_cross_domain_head_blocks(self):
        value = {
            "schema": "yumi.threadmark.ledger.v1.1",
            "scope": "four-domains",
            "required_keys": ["task", "decision"],
            "head_blocks": [
                {
                    "id": "python-agent",
                    "revision_id": "py-1",
                    "source_id": "python-source",
                    "assertions": [
                        {"id": "py-task", "key": "task", "value": "review"},
                        {"id": "py-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "js-agent",
                    "revision_id": "js-1",
                    "source_id": "js-source",
                    "assertions": [
                        {"id": "js-task", "key": "task", "value": "review"},
                        {"id": "js-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "shell-agent",
                    "revision_id": "sh-1",
                    "source_id": "shell-source",
                    "assertions": [
                        {"id": "sh-task", "key": "task", "value": "review"},
                        {"id": "sh-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "review-agent",
                    "revision_id": "review-1",
                    "source_id": "review-source",
                    "assertions": [
                        {"id": "review-task", "key": "task", "value": "review"},
                        {"id": "review-decision", "key": "decision", "value": "continue"},
                    ],
                },
            ],
        }
        result = self.resolve_threadmark(value)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["heads"], ["js-agent", "python-agent", "review-agent", "shell-agent"])
        self.assertEqual(len(result["assertions"]), 8)
        self.assertEqual(
            result["source_ids"],
            ["js-source", "python-source", "review-source", "shell-source"],
        )

    def test_threadmark_detects_conflict_within_one_head(self):
        value = self.base(
            revisions=[
                {
                    "id": "r1",
                    "assertions": [
                        {"id": "a1", "key": "task", "value": "review"},
                        {"id": "a2", "key": "task", "value": "ship"},
                        {"id": "a3", "key": "decision", "value": "continue"},
                    ],
                }
            ]
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["status"], "REVIEW")
        self.assertFalse(result["ready"])
        self.assertEqual(result["key_states"]["task"]["status"], "conflicting")
        self.assertEqual(
            result["key_states"]["task"]["values"],
            [
                {"value": "review", "value_type": "string"},
                {"value": "ship", "value_type": "string"},
            ],
        )
        self.assertTrue(any(item["code"] == "conflicting" for item in result["blockers"]))

    def test_threadmark_detects_conflict_across_supplied_heads(self):
        value = self.base(
            heads=["left", "right"],
            revisions=[
                {
                    "id": "root",
                    "assertions": [{"id": "root-task", "key": "task", "value": "review"}],
                },
                {
                    "id": "left",
                    "parent": "root",
                    "assertions": [{"id": "left-decision", "key": "decision", "value": "continue"}],
                },
                {
                    "id": "right",
                    "parent": "root",
                    "assertions": [{"id": "right-decision", "key": "decision", "value": "pause"}],
                },
            ],
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["heads"], ["left", "right"])
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["key_states"]["decision"]["status"], "conflicting")
        self.assertEqual(
            result["key_states"]["decision"]["values"],
            [
                {"value": "continue", "value_type": "string"},
                {"value": "pause", "value_type": "string"},
            ],
        )
        self.assertEqual(result["key_states"]["task"]["status"], "stable")

    def test_threadmark_withdrawal_is_not_ready_and_is_bound(self):
        value = self.base(
            revisions=[
                {
                    "id": "r1",
                    "assertions": [
                        {"id": "a-task", "key": "task", "value": "review"},
                        {"id": "a-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "r2",
                    "parent": "r1",
                    "source_id": "source-b",
                    "withdrawals": [{"target": "a-decision", "reason": "owner withdrew it"}],
                },
            ]
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["key_states"]["decision"]["status"], "withdrawn")
        self.assertEqual(result["key_states"]["decision"]["assertion_ids"], [])
        self.assertEqual(result["key_states"]["decision"]["revision_ids"], [])
        self.assertEqual(result["key_states"]["decision"]["source_ids"], [])
        withdrawal = result["key_states"]["decision"]["withdrawals"][0]
        self.assertEqual(withdrawal["target"], "a-decision")
        self.assertTrue(withdrawal["effective"])
        self.assertEqual(withdrawal["source_verification"], "unverified")

    def test_threadmark_missing_key_is_not_ready(self):
        value = self.base(
            required_keys=["task", "decision", "owner"],
            revisions=[
                {
                    "id": "r1",
                    "assertions": [{"id": "a-task", "key": "task", "value": "review"}],
                }
            ],
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["key_states"]["owner"]["status"], "missing")
        self.assertTrue(any(item["key"] == "owner" for item in result["blockers"]))

    def test_threadmark_typed_json_equality_does_not_coerce_values(self):
        value = self.base(
            heads=["left", "right"],
            revisions=[
                {
                    "id": "left",
                    "assertions": [
                        {"id": "left-task", "key": "task", "value": 1},
                        {"id": "left-decision", "key": "decision", "value": "continue"},
                    ],
                },
                {
                    "id": "right",
                    "assertions": [
                        {"id": "right-task", "key": "task", "value": True},
                        {"id": "right-decision", "key": "decision", "value": "continue"},
                    ],
                },
            ],
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["key_states"]["task"]["status"], "conflicting")
        self.assertEqual(
            result["key_states"]["task"]["values"],
            [
                {"value": True, "value_type": "boolean"},
                {"value": 1, "value_type": "integer"},
            ],
        )
        self.assertFalse(result["ready"])

    def test_threadmark_required_key_cap_is_explicit(self):
        result = self.resolve_threadmark(
            self.base(required_keys=[f"key-{index}" for index in range(9)])
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["ready"])
        self.assertIn("required_key_count", result["diagnostics"]["limits"])

    def test_threadmark_missing_predecessor_is_invalid_not_ready(self):
        result = self.resolve_threadmark(
            self.base(
                heads=["r2"],
                revisions=[
                    {
                        "id": "r2",
                        "parent": "not-present",
                        "assertions": [
                            {"id": "a", "key": "task", "value": "review"},
                            {"id": "b", "key": "decision", "value": "continue"},
                        ],
                    }
                ],
            )
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["ready"])
        self.assertIn("not-present", result["diagnostics"]["missing_references"])

    def test_threadmark_output_cap_returns_detail_required_without_ready(self):
        huge = "x" * 5000
        value = self.base(
            required_keys=["task"],
            revisions=[
                {
                    "id": "r1",
                    "assertions": [{"id": "a", "key": "task", "value": huge}],
                }
            ],
        )
        result = self.resolve_threadmark(value)
        self.assertEqual(result["status"], "DETAIL_REQUIRED")
        self.assertEqual(result["decision"], "DETAIL_REQUIRED")
        self.assertTrue(result["detail_required"])
        self.assertFalse(result["ready"])
        self.assertNotIn("value", json.dumps(result))
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), self.module.THREADMARK_MAX_OUTPUT_BYTES)

    def test_threadmark_fresh_cli_is_deterministic_and_rejects_output_file(self):
        payload = json.dumps(self.base(), sort_keys=True)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        outputs = []
        for _ in range(2):
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-S", str(SCRIPT), "threadmark", "--input", "-"],
                input=payload,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])
        rejected = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(SCRIPT), "threadmark", "--input", "-", "--output", "x"],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["status"], "INVALID")

    def test_public_scenarios_match_declared_statuses(self):
        scenarios = json.loads(
            (ROOT / "examples" / "scenarios.json").read_text(encoding="utf-8")
        )["scenarios"]
        for scenario in scenarios:
            if "ledger" not in scenario:
                self.assertIn("ledger_shape", scenario)
                self.assertEqual(scenario["expected_status"], "DETAIL_REQUIRED")
                continue
            result = self.resolve_threadmark(scenario["ledger"])
            self.assertEqual(result["status"], scenario["expected_status"], scenario["id"])
            for key, expected in scenario.get("expected_key_status", {}).items():
                self.assertEqual(result["key_states"][key]["status"], expected, scenario["id"])


if __name__ == "__main__":
    unittest.main()


