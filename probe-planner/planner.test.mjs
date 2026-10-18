import test from "node:test";
import assert from "node:assert/strict";
import { planDiagnostic } from "./planner.mjs";

function allEquivalent(model, indices) {
  if (indices.length <= 1) return true;
  const firstId = model.hypotheses[indices[0]].id;
  for (let position = 1; position < indices.length; position += 1) {
    const candidateId = model.hypotheses[indices[position]].id;
    for (const probe of model.probes) {
      if (probe.outcomes[firstId] !== probe.outcomes[candidateId]) return false;
    }
  }
  return true;
}

// No memoization: independently enumerate every informative first probe
// and every recursively possible branch policy for tiny models.
function exhaustiveScore(model, indices = model.hypotheses.map((_, i) => i)) {
  if (allEquivalent(model, indices)) return 0;
  let stateWeight = 0;
  for (const index of indices) stateWeight += model.hypotheses[index].weight;
  let best = Number.POSITIVE_INFINITY;
  for (const probe of model.probes) {
    const byOutcome = new Map();
    const blocks = [];
    for (const index of indices) {
      const hypothesisId = model.hypotheses[index].id;
      const outcome = probe.outcomes[hypothesisId];
      let block = byOutcome.get(outcome);
      if (block === undefined) {
        block = [];
        byOutcome.set(outcome, block);
        blocks.push(block);
      }
      block.push(index);
    }
    if (blocks.length < 2) continue;
    let candidate = probe.cost * stateWeight;
    for (const block of blocks) candidate += exhaustiveScore(model, block);
    if (candidate < best) best = candidate;
  }
  if (!Number.isFinite(best)) throw new Error("exhaustive enumerator found no informative probe");
  return best;
}

test("rejects malformed, non-integer, duplicate, extra and overlong fields", () => {
  assert.throws(() => planDiagnostic({ hypotheses: [], probes: [] }), RangeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1.5 }], probes: [] }), RangeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1_000_001 }], probes: [] }), RangeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }, { id: "a", weight: 2 }], probes: [] }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }], probes: [], extra: true }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1, extra: true }], probes: [] }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }], probes: [{ id: "p", cost: 1.5, outcomes: { a: "x", b: "y" } }] }), RangeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }], probes: [{ id: "p", cost: 1, outcomes: { a: "x", b: "y", extra: "z" } }] }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }], probes: [{ id: "p", cost: 1, outcomes: { a: "x" } }] }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "h".repeat(65), weight: 1 }], probes: [] }), TypeError);
  assert.throws(() => planDiagnostic({ hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }], probes: [{ id: "p", cost: 1, outcomes: { a: "x".repeat(65), b: "y" } }] }), TypeError);
});

test("uses stable input-order tie-breaking for equal exact scores", () => {
  const plan = planDiagnostic({
    hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }],
    probes: [
      { id: "first", cost: 1, outcomes: { a: "left", b: "right" } },
      { id: "second", cost: 1, outcomes: { a: "one", b: "two" } },
    ],
  });
  assert.equal(plan.expectedCostNumerator, 2);
  assert.equal(plan.expectedCostDenominator, 2);
  assert.equal(plan.expectedCost, 1);
  assert.equal(plan.policy.probeId, "first");
  assert.deepEqual(plan.policy.outcomes.map((branch) => branch.outcome), ["left", "right"]);
});

test("reports indistinguishable groups without claiming false certainty", () => {
  const plan = planDiagnostic({
    hypotheses: [{ id: "h1", weight: 1 }, { id: "h2", weight: 2 }, { id: "h3", weight: 1 }],
    probes: [{ id: "q", cost: 2, outcomes: { h1: "same", h2: "same", h3: "different" } }],
  });
  assert.deepEqual(plan.undistinguishableGroups, [{
    hypothesisIds: ["h1", "h2"], totalWeight: 3, probability: 0.75,
    probabilityNumerator: 3, probabilityDenominator: 4,
  }]);
  const sameBranch = plan.policy.outcomes.find((branch) => branch.outcome === "same");
  assert.equal(sameBranch.next.kind, "undistinguishable");
  assert.deepEqual(sameBranch.next.hypothesisIds, ["h1", "h2"]);
  assert.equal(sameBranch.next.expectedCostNumerator, 0);
  const differentBranch = plan.policy.outcomes.find((branch) => branch.outcome === "different");
  assert.equal(differentBranch.next.kind, "resolved");
  assert.equal(differentBranch.next.hypothesisId, "h3");
});

test("finds a known adaptive optimum with exact integer scoring", () => {
  const plan = planDiagnostic({
    hypotheses: [{ id: "a", weight: 4 }, { id: "b", weight: 3 }, { id: "c", weight: 3 }],
    probes: [
      { id: "cheap-split", cost: 1, outcomes: { a: "alone", b: "pair", c: "pair" } },
      { id: "expensive-full", cost: 4, outcomes: { a: "a", b: "b", c: "c" } },
    ],
  });
  assert.equal(plan.expectedCostNumerator, 34);
  assert.equal(plan.expectedCostDenominator, 10);
  assert.equal(plan.expectedCost, 3.4);
  assert.equal(plan.policy.probeId, "cheap-split");
  const pairBranch = plan.policy.outcomes.find((branch) => branch.outcome === "pair");
  assert.equal(pairBranch.next.kind, "probe");
  assert.equal(pairBranch.next.probeId, "expensive-full");
  assert.equal(pairBranch.next.expectedCostNumerator, 24);
  assert.equal(pairBranch.next.expectedCostDenominator, 6);
});

test("matches an independent exhaustive enumerator on deterministic tiny models", () => {
  const models = [
    {
      hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 2 }, { id: "c", weight: 3 }],
      probes: [
        { id: "p", cost: 2, outcomes: { a: "x", b: "y", c: "y" } },
        { id: "q", cost: 1, outcomes: { a: "u", b: "u", c: "v" } },
      ],
    },
    {
      hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 2 }, { id: "c", weight: 1 }, { id: "d", weight: 3 }],
      probes: [
        { id: "p1", cost: 1, outcomes: { a: "0", b: "0", c: "1", d: "1" } },
        { id: "p2", cost: 2, outcomes: { a: "0", b: "1", c: "0", d: "1" } },
        { id: "p3", cost: 3, outcomes: { a: "0", b: "1", c: "1", d: "0" } },
      ],
    },
    { hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 4 }], probes: [] },
    {
      hypotheses: [{ id: "a", weight: 1 }, { id: "b", weight: 1 }, { id: "c", weight: 2 }, { id: "d", weight: 2 }],
      probes: [
        { id: "p", cost: 3, outcomes: { a: "x", b: "x", c: "y", d: "y" } },
        { id: "q", cost: 1, outcomes: { a: "u", b: "v", c: "u", d: "v" } },
      ],
    },
  ];
  for (const model of models) {
    const expectedScore = exhaustiveScore(model);
    const plan = planDiagnostic(model);
    assert.equal(plan.expectedCostNumerator, expectedScore);
    assert.equal(plan.expectedCostDenominator, model.hypotheses.reduce((sum, h) => sum + h.weight, 0));
  }
});

test("reconciles illustrative release-digest hypotheses without asserting release truth", () => {
  // Public context only: both destinations visibly contain v0.17.6,
  // while provider/origin digest parity remains unverified.
  const plan = planDiagnostic({
    hypotheses: [
      { id: "same-authoritative", weight: 6 },
      { id: "pypi-differs-from-origin", weight: 2 },
      { id: "github-differs-from-origin", weight: 2 },
    ],
    probes: [
      { id: "cross-system-digest-compare", cost: 1, outcomes: {
        "same-authoritative": "match", "pypi-differs-from-origin": "mismatch", "github-differs-from-origin": "mismatch",
      } },
      { id: "compare-pypi-to-origin", cost: 2, outcomes: {
        "same-authoritative": "match", "pypi-differs-from-origin": "mismatch", "github-differs-from-origin": "match",
      } },
    ],
  });
  assert.equal(plan.expectedCostNumerator, 18);
  assert.equal(plan.expectedCostDenominator, 10);
  assert.equal(plan.expectedCost, 1.8);
  assert.equal(plan.policy.probeId, "cross-system-digest-compare");
  const branch = plan.policy.outcomes.find((b) => b.outcome === "mismatch");
  assert.equal(branch.probabilityNumerator, 4);
  assert.equal(branch.probabilityDenominator, 10);
  assert.equal(branch.probability, 0.4);
  assert.deepEqual(branch.next.hypothesisIds, ["pypi-differs-from-origin", "github-differs-from-origin"]);
  assert.equal(branch.next.kind, "probe");
  assert.equal(branch.next.probeId, "compare-pypi-to-origin");
  assert.deepEqual(branch.next.outcomes.map((b) => b.next.hypothesisId), ["pypi-differs-from-origin", "github-differs-from-origin"]);
  assert.ok(plan.assumptions.some((a) => a.includes("caller-supplied")));
  assert.ok(plan.assumptions.some((a) => a.includes("identical complete outcome vectors")));
});
