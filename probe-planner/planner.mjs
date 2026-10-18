/*
Conceptual background only:
- https://d3m-research-group.github.io/odtlearn/ describes Python/MIO
  learning of classification and prescriptive trees.
- https://d3m-research-group.github.io/odtlearn/installation.html documents
  its Gurobi and CBC/Python-MIP installation alternatives.
- https://proceedings.mlr.press/v202/demirovic23a.html describes bounded-depth
  optimal decision-tree learning with a dynamic-programming approach.

This independently authored module solves a different bounded problem:
adaptive diagnostics over caller-supplied deterministic outcomes, integer
costs, and relative integer weights. It does not learn from data or claim
equivalence or superiority to those richer learners.
*/

const MODEL_KEYS = new Set(["hypotheses", "probes"]);
const HYPOTHESIS_KEYS = new Set(["id", "weight"]);
const PROBE_KEYS = new Set(["id", "cost", "outcomes"]);
const MAX_ITEMS = 12;
const MAX_INTEGER = 1_000_000;
const MAX_STRING_LENGTH = 64;

const OWN = Object.prototype.hasOwnProperty;

function isPlainObject(value) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function assertExactRecord(value, expectedKeys, label) {
  if (!isPlainObject(value)) throw new TypeError(`${label} must be a plain object`);
  const names = Object.getOwnPropertyNames(value);
  const symbols = Object.getOwnPropertySymbols(value);
  if (symbols.length !== 0 || names.length !== expectedKeys.size) {
    throw new TypeError(`${label} has unexpected fields`);
  }
  for (const name of names) {
    if (!expectedKeys.has(name)) throw new TypeError(`${label} has unexpected field ${name}`);
  }
  for (const key of expectedKeys) {
    if (!OWN.call(value, key)) throw new TypeError(`${label} is missing field ${key}`);
  }
}

function assertBoundedArray(value, minimum, maximum, label) {
  if (!Array.isArray(value)) throw new TypeError(`${label} must be an array`);
  if (value.length < minimum || value.length > maximum) {
    throw new RangeError(`${label} length must be between ${minimum} and ${maximum}`);
  }
}

function assertIdentifier(value, label) {
  if (typeof value !== "string" || value.length < 1 || value.length > MAX_STRING_LENGTH) {
    throw new TypeError(`${label} must contain 1 through ${MAX_STRING_LENGTH} UTF-16 code units`);
  }
}

function assertBoundedInteger(value, label) {
  if (typeof value !== "number") throw new TypeError(`${label} must be a number`);
  if (!Number.isSafeInteger(value) || value < 1 || value > MAX_INTEGER) {
    throw new RangeError(`${label} must be an integer between 1 and ${MAX_INTEGER}`);
  }
}

function assertOutcomeRecord(value, hypothesisIndex, hypothesisCount, label) {
  if (!isPlainObject(value)) throw new TypeError(`${label} must be an ordinary object`);
  const names = Object.getOwnPropertyNames(value);
  const symbols = Object.getOwnPropertySymbols(value);
  if (symbols.length !== 0 || names.length !== hypothesisCount) {
    throw new TypeError(`${label} must contain exactly the hypothesis IDs`);
  }
  for (const name of names) {
    if (!hypothesisIndex.has(name)) throw new TypeError(`${label} contains an unknown hypothesis ID`);
  }
}

function assertSafeScore(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) throw new RangeError(`${label} exceeded the safe integer range`);
}

function finiteProbability(numerator, denominator, label) {
  const value = numerator / denominator;
  if (!Number.isFinite(value) || value <= 0 || value > 1) {
    throw new RangeError(`${label} is not representable as a probability`);
  }
  return value;
}

function decimalExpectedCost(score, weight, label) {
  const value = score / weight;
  if (!Number.isFinite(value)) throw new RangeError(`${label} is not finite`);
  return value;
}

// The contract is parsed JSON data, not arbitrary hostile JS proxies/getters.
export function planDiagnostic(model) {
  assertExactRecord(model, MODEL_KEYS, "model");
  const hypothesisInput = model.hypotheses;
  const probeInput = model.probes;
  assertBoundedArray(hypothesisInput, 1, MAX_ITEMS, "hypotheses");
  assertBoundedArray(probeInput, 0, MAX_ITEMS, "probes");
  const hypothesisIds = [];
  const weights = [];
  const hypothesisIndex = new Map();
  for (let index = 0; index < hypothesisInput.length; index += 1) {
    const entry = hypothesisInput[index];
    const label = `hypotheses[${index}]`;
    assertExactRecord(entry, HYPOTHESIS_KEYS, label);
    assertIdentifier(entry.id, `${label}.id`);
    assertBoundedInteger(entry.weight, `${label}.weight`);
    if (hypothesisIndex.has(entry.id)) throw new TypeError(`duplicate hypothesis ID ${entry.id}`);
    hypothesisIndex.set(entry.id, index);
    hypothesisIds.push(entry.id);
    weights.push(entry.weight);
  }

  const probeIds = [];
  const costs = [];
  const probeOutcomes = [];
  const probeIndex = new Map();
  for (let index = 0; index < probeInput.length; index += 1) {
    const entry = probeInput[index];
    const label = `probes[${index}]`;
    assertExactRecord(entry, PROBE_KEYS, label);
    assertIdentifier(entry.id, `${label}.id`);
    assertBoundedInteger(entry.cost, `${label}.cost`);
    if (probeIndex.has(entry.id)) throw new TypeError(`duplicate probe ID ${entry.id}`);
    assertOutcomeRecord(entry.outcomes, hypothesisIndex, hypothesisIds.length, `${label}.outcomes`);
    const values = new Array(hypothesisIds.length);
    for (let hypothesis = 0; hypothesis < hypothesisIds.length; hypothesis += 1) {
      const hypothesisId = hypothesisIds[hypothesis];
      if (!OWN.call(entry.outcomes, hypothesisId)) {
        throw new TypeError(`${label}.outcomes is missing hypothesis ${hypothesisId}`);
      }
      const value = entry.outcomes[hypothesisId];
      if (typeof value !== "string" || value.length < 1 || value.length > MAX_STRING_LENGTH) {
        throw new TypeError(`${label}.outcomes.${hypothesisId} must contain 1 through ${MAX_STRING_LENGTH} UTF-16 code units`);
      }
      values[hypothesis] = value;
    }
    probeIndex.set(entry.id, index);
    probeIds.push(entry.id);
    costs.push(entry.cost);
    probeOutcomes.push(values);
  }

  const hypothesisCount = hypothesisIds.length;
  const probeCount = probeIds.length;
  const stateCount = 1 << hypothesisCount;
  const subsetWeights = new Array(stateCount);
  subsetWeights[0] = 0;
  for (let mask = 1; mask < stateCount; mask += 1) {
    let total = 0;
    for (let hypothesis = 0; hypothesis < hypothesisCount; hypothesis += 1) {
      if ((mask & (1 << hypothesis)) !== 0) {
        total += weights[hypothesis];
        assertSafeScore(total, "hypothesis weight sum");
      }
    }
    subsetWeights[mask] = total;
  }
  const totalWeight = subsetWeights[stateCount - 1];

  function sameOutcomeVector(left, right) {
    for (let probe = 0; probe < probeCount; probe += 1) {
      if (probeOutcomes[probe][left] !== probeOutcomes[probe][right]) return false;
    }
    return true;
  }

  const undistinguishableGroups = [];
  const grouped = new Array(hypothesisCount).fill(false);
  for (let first = 0; first < hypothesisCount; first += 1) {
    if (grouped[first]) continue;
    const members = [first];
    grouped[first] = true;
    for (let candidate = first + 1; candidate < hypothesisCount; candidate += 1) {
      if (!grouped[candidate] && sameOutcomeVector(first, candidate)) {
        grouped[candidate] = true;
        members.push(candidate);
      }
    }
    if (members.length > 1) {
      let groupMask = 0;
      for (const member of members) groupMask |= 1 << member;
      const groupWeight = subsetWeights[groupMask];
      undistinguishableGroups.push({
        hypothesisIds: members.map((member) => hypothesisIds[member]),
        totalWeight: groupWeight,
        probability: finiteProbability(groupWeight, totalWeight, "global group probability"),
        probabilityNumerator: groupWeight,
        probabilityDenominator: totalWeight,
      });
    }
  }

  function partition(mask, probe) {
    const blocks = [];
    const byOutcome = new Map();
    for (let hypothesis = 0; hypothesis < hypothesisCount; hypothesis += 1) {
      if ((mask & (1 << hypothesis)) === 0) continue;
      const outcome = probeOutcomes[probe][hypothesis];
      let block = byOutcome.get(outcome);
      if (block === undefined) {
        block = { outcome, mask: 0 };
        byOutcome.set(outcome, block);
        blocks.push(block);
      }
      block.mask |= 1 << hypothesis;
    }
    return blocks;
  }

  function stateIsTerminal(mask) {
    let first = -1;
    for (let hypothesis = 0; hypothesis < hypothesisCount; hypothesis += 1) {
      if ((mask & (1 << hypothesis)) !== 0) {
        first = hypothesis;
        break;
      }
    }
    if (first === -1) return true;
    for (let hypothesis = first + 1; hypothesis < hypothesisCount; hypothesis += 1) {
      if ((mask & (1 << hypothesis)) === 0) continue;
      if (!sameOutcomeVector(first, hypothesis)) return false;
    }
    return true;
  }

  const memo = new Map();
  function solve(mask) {
    if (memo.has(mask)) return memo.get(mask);
    if (stateIsTerminal(mask)) {
      const terminal = { score: 0, probe: -1, blocks: null };
      memo.set(mask, terminal);
      return terminal;
    }
    const stateWeight = subsetWeights[mask];
    let bestProbe = -1;
    let bestScore = 0;
    let bestBlocks = null;
    for (let probe = 0; probe < probeCount; probe += 1) {
      const blocks = partition(mask, probe);
      if (blocks.length < 2) continue;
      // Exact unnormalized Bellman score C(S)=cost(q)*W(S)+sum C(S_o).
      let candidateScore = costs[probe] * stateWeight;
      assertSafeScore(candidateScore, "candidate expected-cost score");
      for (const block of blocks) {
        candidateScore += solve(block.mask).score;
        assertSafeScore(candidateScore, "candidate expected-cost score");
      }
      if (bestProbe === -1 || candidateScore < bestScore) {
        bestProbe = probe;
        bestScore = candidateScore;
        bestBlocks = blocks;
      }
    }
    if (bestProbe === -1) throw new Error("nonterminal state has no informative probe");
    const result = { score: bestScore, probe: bestProbe, blocks: bestBlocks };
    memo.set(mask, result);
    return result;
  }

  function idsForMask(mask) {
    const ids = [];
    for (let hypothesis = 0; hypothesis < hypothesisCount; hypothesis += 1) {
      if ((mask & (1 << hypothesis)) !== 0) ids.push(hypothesisIds[hypothesis]);
    }
    return ids;
  }

  function materialize(mask) {
    const result = solve(mask);
    const stateWeight = subsetWeights[mask];
    const hypothesisIdsForState = idsForMask(mask);
    const expectedCost = decimalExpectedCost(result.score, stateWeight, "policy expected cost");
    if (result.probe === -1) {
      const exactCostFields = {
        expectedCost,
        expectedCostNumerator: result.score,
        expectedCostDenominator: stateWeight,
      };
      if (hypothesisIdsForState.length === 1) {
        return {
          kind: "resolved", hypothesisId: hypothesisIdsForState[0],
          hypothesisIds: hypothesisIdsForState, totalWeight: stateWeight, ...exactCostFields,
        };
      }
      return {
        kind: "undistinguishable", hypothesisIds: hypothesisIdsForState,
        totalWeight: stateWeight, ...exactCostFields,
      };
    }
    const outcomes = result.blocks.map((block) => {
      const childWeight = subsetWeights[block.mask];
      return {
        outcome: block.outcome,
        probability: finiteProbability(childWeight, stateWeight, "branch probability"),
        probabilityNumerator: childWeight,
        probabilityDenominator: stateWeight,
        next: materialize(block.mask),
      };
    });
    return {
      kind: "probe", probeId: probeIds[result.probe], cost: costs[result.probe],
      expectedCost, expectedCostNumerator: result.score,
      expectedCostDenominator: stateWeight, hypothesisIds: hypothesisIdsForState,
      totalWeight: stateWeight, outcomes,
    };
  }

  const rootMask = stateCount - 1;
  const root = solve(rootMask);
  return {
    schema: "yumi.diagnostic-plan.v1",
    assumptions: [
      "Outcomes are deterministic and caller-supplied.",
      "Hypothesis weights are treated as relative prior masses and normalized only for probabilities.",
      "Expected cost is conditional on the supplied model.",
      "Undistinguishable means identical complete outcome vectors across the supplied probes only.",
      "Policy branch probabilities are conditional on the parent policy node.",
      "Global undistinguishableGroups reports only non-singleton equivalence classes.",
      "Weights and costs are positive integers from 1 through 1,000,000.",
      "Hypothesis IDs, probe IDs, and outcome strings contain 1 through 64 UTF-16 code units.",
      "Expected-cost numerators and denominators use exact safe integer arithmetic.",
      "Decimal expectedCost and probability fields are IEEE-754 approximations of adjacent exact integer ratios.",
    ],
    totalWeight,
    expectedCost: decimalExpectedCost(root.score, totalWeight, "root expected cost"),
    expectedCostNumerator: root.score,
    expectedCostDenominator: totalWeight,
    undistinguishableGroups,
    policy: materialize(rootMask),
  };
}
