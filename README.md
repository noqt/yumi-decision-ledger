# Yumi Decision Ledger

Use this offline Python tool when an agent handoff refers to a decision that may
have been superseded, withdrawn, lost behind a missing predecessor, or split
across conflicting heads. It resolves an explicitly supplied JSON revision
ledger and reports whether every required key has one usable current value.

The input cost is deliberate: callers provide a scope, the required keys, and
the relevant revisions or head blocks with assertion, source, supersession, and
withdrawal identifiers. Yumi does not scrape chat history or infer a ledger
from prose. A `READY` result means consistency within the supplied records
only—not truth, authenticated provenance, freshness, completeness, permission,
safety, or task completion.

## First value: catch a withdrawn handoff decision

This small fictional ledger is the existing `withdrawn-decision` example in
[`examples/scenarios.json`](examples/scenarios.json). Revision `withdraw-r1`
asserts that the agent should continue; its successor withdraws that assertion.
Save the following as `ledger.json`:

```json
{
  "scope": "withdrawal",
  "required_keys": ["decision"],
  "revisions": [
    {
      "id": "withdraw-r1",
      "assertions": [
        {"id": "withdraw-a", "key": "decision", "value": "continue"}
      ]
    },
    {
      "id": "withdraw-r2",
      "parent": "withdraw-r1",
      "withdrawals": [
        {"target": "withdraw-a", "reason": "supplied correction"}
      ]
    }
  ]
}
```

Run the single-file resolver with Python 3.11 or newer:

```text
python3 -I -B -S yumi_continuity.py threadmark --input ledger.json
```

The actual canonical JSON result includes:

```json
{
  "decision": "REVIEW",
  "blockers": [
    {"code": "withdrawn", "heads": ["withdraw-r2"], "key": "decision"}
  ],
  "key_states": {
    "decision": {
      "status": "withdrawn",
      "withdrawals": [
        {
          "effective": true,
          "revision_id": "withdraw-r2",
          "target": "withdraw-a"
        }
      ]
    }
  },
  "revision_ids": ["withdraw-r1", "withdraw-r2"],
  "status": "REVIEW"
}
```

Exit code 2 signals that review is required: the supplied records say the only
required decision was withdrawn. The caller must handle `REVIEW`. The result
preserves the revision and withdrawal-target identifiers needed for review, but
it does not decide what the next decision should be.

## Optional origin-bound command receipt

`origin_receipt.py` is a separate, opt-in component for callers who want to
retain facts from one foreground command and later recheck declared file
identities without repeating that command. It is not part of the ledger
resolver or native skill route, and nothing invokes it automatically.

From the package directory, name an existing authorized workspace explicitly.
Use paths relative to that workspace for receipt, inputs, and output artifacts:

```text
python3.12 -B origin_receipt.py run --workspace "/absolute/path/to/authorized/workspace" --receipt "receipts/run.json" --input "src/job.py" --artifact "out/result.json" --timeout 30 -- python3 -B "src/job.py"
python3.12 -B origin_receipt.py check --workspace "/absolute/path/to/authorized/workspace" --receipt "receipts/run.json" --input "src/job.py" --artifact "out/result.json"
```

The workspace and the receipt's parent directory must already exist, and an
existing receipt is never overwritten. Repeat each declared input and artifact
when checking. The command after `--` is direct argv; `run` calls it once with
`shell=False`, in the named workspace, and records the observed completion,
child exit code or timeout, bounded stdout/stderr, declared-input identities
before and after, and declared-artifact identities after completion. A captured
failure remains a fact in the receipt.

The child inherits the caller's environment and permissions; this is not a
sandbox and imposes no child network or workspace-write restriction. The
receipt records argv and bounded stdout/stderr, which can expose sensitive
values; review the command and output before retaining a receipt. Environment
variables and a full transcript are not stored.

At least one input is required for a scope to be `BOUND`. Artifact-only and
empty scopes are `UNBOUND`; execution and any output identity facts are still
retained. For a valid receipt, the check reports `MATCH` only when the declared
scope matches and every recorded/current identity is present and stable.
Otherwise it reports a conservative state such as `MISMATCH`,
`INDETERMINATE`, or `UNBOUND`, with named differences. A matching identity
does not mean the command was correct.

`check` reads only the explicit receipt and workspace. It never executes or
replays command strings or other receipt values. `run` exits 0 when it writes
the receipt, even when the child failed or timed out; invocation or receipt
errors exit 2. `check` exits 0 for readable outcomes (`MATCH`, `MISMATCH`,
`UNBOUND`, or `INDETERMINATE`), not as an endorsement. A malformed or
tampered receipt exits 2. The receipt digest is local integrity checking, not
a signature or authentication.

New receipts use format `origin-result-receipt/v2` and include a bounded
`producer` object with `name`, `software_version`, `repository`, and
`format_documentation` strings. The current self-reported values identify
`origin-receipt` version `1.1.0`, its repository locator, and the relative
descriptor name `origin-receipt.json`. These values are informational, not
authenticated. The reader validates their shape and reports them as
`self_reported`; locator strings remain data and are never fetched or executed.

The reader continues to accept `origin-result-receipt/v1` receipts that do not
contain producer metadata and reports that metadata as `not_recorded`. A reader
that accepts only v1 does not accept newly written v2 receipts.

The origin component uses Python 3.12 or newer and the standard library. It was
tested once on Windows with Python 3.12.5. This is not certification for every
Python 3.12 build or host; the repository's earlier Python 3.11+ compatibility
observation applies only to the legacy ledger components below.

## What it does

The existing ledger components provide separate Python 3.11+ command-line
tools that:

- resolve current state across revision chains and branches;
- report retractions, missing predecessors, unresolved forks, and provenance;
- emit a deterministic handoff summary (called a **Threadmark**) for required
  task keys;
- export and check a bounded handoff card; and
- check a bounded settings snapshot against freshly resolved required keys.

The existing resolver is one inspectable Python file. The handoff exporter,
handoff freshness checker, and snapshot checker are separate adjacent scripts.
These legacy ledger components use only the Python standard library and have no
network access, telemetry, output-file mode, or private Yumi service
dependency. Those statements do not describe the behavior of
`origin_receipt.py`'s child command.

## Legacy ledger commands

Resolve a ledger:

```text
python3 -I -B -S yumi_continuity.py resolve --input ledger.json
```

Produce a Threadmark for the ledger's declared required keys:

```text
python3 -I -B -S yumi_continuity.py threadmark --input ledger.json
```

Export a bounded handoff card from a Threadmark:

```text
python3 -I -B -S threadmark_handoff.py export --input threadmark.json
```

Check a received card against a newly supplied ledger:

```text
python3 -I -B -S threadmark_verify.py check --card handoff-card.json --ledger current-ledger.json
```

Check a bounded settings snapshot against a newly supplied ledger:

```text
python3 -I -B -S snapshot_check.py check --snapshot settings-snapshot.json --ledger current-ledger.json
```

Each legacy ledger command writes canonical JSON to stdout. Use `-` instead of
a path to read stdin. Each checker accepts stdin for either input, but not both
in the same invocation. Run the code only when you are already authorized to
inspect and execute it and to read the supplied input. The `-I -B -S` flags
reduce ambient Python behavior; they are not an operating-system sandbox.

## Package, provenance, and optional skill installation

This bundle is packaging version 1.4.0. It adds a separate opt-in
origin-bound command-receipt component; it does not change the existing ledger
capabilities. The snapshot applicability checker remains v1.0, the functional
handoff freshness checker remains v1.2, and the resolver and handoff-card base
components remain the unchanged, already-public Yumi continuity v1.1
capability. The two ledger checkers reuse those base components; neither is
independent validation of them. Immutable base source originated in
[`noqt/noqt`](https://github.com/noqt/noqt): the resolver bundle is pinned at
[`c9496a7d`](https://github.com/noqt/noqt/tree/c9496a7d599af07be6c170e3894de0140d3079b9/yumi/continuity/v1.1)
and the handoff-card addition at
[`ba2c990d`](https://github.com/noqt/noqt/tree/ba2c990de865cf78377486796690535a4a26874b/yumi/continuity/v1.1).
The intended canonical discovery entrypoint for this standalone package is
<https://github.com/noqt/yumi-decision-ledger>. Immutable code output may still
carry its earlier `noqt/noqt` documentation URL as origin metadata.

The repository root is also packaged as a native skill named
`yumi-decision-ledger` for explicit ledger reconciliation and handoff or
snapshot checks. It is not a generic memory, database, or JSON/text diff skill.
The skill does not invoke the origin-receipt component.

An installer that accepts the standard skills repository form can install the
current repository state with:

```text
npx skills add noqt/yumi-decision-ledger --skill yumi-decision-ledger
```

That optional convenience command is not a commit pin. When reproducibility
matters, inspect an exact commit first, verify `SKILL.md` and the manifest
inventory, then install from that pinned local checkout using the installer's
local-source flow. Installation does not grant permission to inspect a ledger
or run its contents.

### Observed Codex compatibility

One Windows project-copy check used `skills` 1.7.2 with `--agent codex
--copy`. From that installed project location, the resolver and handoff
freshness checker worked on fictional data under Python 3.12.5. This is one
compatibility observation for those legacy components, not automatic
activation, certification of other hosts, independent discovery, or adoption.

That install converted all 12 published LF text files to CRLF. Their
LF-normalized content matched the published files, but their installed byte
hashes did not. SHA-256 values in [`manifest.json`](manifest.json) identify the
original published LF bytes, not byte-identical Windows copies.

## Check a handoff card

`threadmark_verify.py` reads one bounded card and one bounded ledger, then
recomputes the ledger's current Threadmark with the same resolver used by
`yumi_continuity.py`. It does not treat the old card as a receipt of truth, and
a matching card digest cannot override a changed, missing, withdrawn, or
conflicting typed state. The checker reports per-key state and provenance
drift, the actual card and ledger byte digests, and the card's unverified
receipt/input digest claims. It never follows source IDs or executes values.

The result status is one of:

- `UNCHANGED`: both inputs are usable and the scope, exact required-key set,
  typed per-key states, declared current-head set, and required-key head and
  reference details match. Each READY key's head observations must cover
  exactly the card's unique declared heads, and that declared set is compared
  with the freshly resolved ledger heads. Top-level `assertion_ids`,
  `revision_ids`, and `source_ids` are shape-checked, but unrelated global
  extras are not compared: freshness compares references attached to
  required-key states only. This describes consistency within the supplied
  files only.
- `STALE`: the current ledger resolves ready, but its scope, required keys, or
  per-key state/provenance differs from the card.
- `BLOCKED`: either side is not ready, or the resolver cannot retain enough
  bounded detail to compare safely. Missing detail is never treated as an
  empty matching state.
- `INVALID`: an input is malformed, exceeds its bound, or fails the existing
  card/ledger contract.

Only `UNCHANGED` exits with code 0; `STALE` and `BLOCKED` exit with code 1, and
`INVALID` exits with code 2. Digest links are advisory: they are not
authentication, freshness, completeness, permission, or safety checks. The
result's capability identifies this v1.2 checker and its canonical discovery
page; the separately nested base capability retains the handoff exporter's
origin metadata.

## Check a settings snapshot

`snapshot_check.py` accepts exactly two JSON inputs. The snapshot root must be
an object with only `scope` (a nonempty string of at most 256 characters) and
`settings` (an object whose keys are nonempty strings of at most 256
characters and whose values are JSON data). The 64 KiB snapshot-input bound
also bounds the settings count; the unchanged resolver allows at most eight
required ledger keys. Both inputs are limited to 64 KiB and the canonical
result to 16 KiB. Either input may be `-` for stdin, but both cannot use stdin
at once. There is no output-file option.

The checker invokes the unchanged `threadmark_ledger` resolver before making
any scope, key-set, or value applicability comparison. It never selects a
first head or infers authority from input order. Equality uses the resolver's
canonical JSON type-aware semantics: for example, `true` is not equal to `1`,
object key order does not matter, and array order does.

The result status is one of:

- `UNCHANGED`: the current ledger resolves `READY`, snapshot and ledger scopes
  match exactly, the snapshot settings key set exactly equals the ledger's
  required keys, and every value matches with JSON-type-aware equality.
- `STALE`: the resolver is `READY`, but scope, required key set, or a typed
  setting value differs.
- `BLOCKED`: the resolver returns `REVIEW` or `DETAIL_REQUIRED`, including
  missing, withdrawn, conflicting, or insufficient required-key detail. Such
  cases are not called stale. A result-too-large fallback is also blocked and
  explicitly marks its details incomplete.
- `INVALID`: malformed, oversized, or contract-invalid input, or an invalid
  ledger/resolver result.

Only `UNCHANGED` exits with code 0; `STALE` and `BLOCKED` exit with code 1, and
`INVALID` exits with code 2. The result reports actual input byte counts and
SHA-256 digests when the complete input was read, plus per-input `complete`
markers. For an oversized input, `bytes` is the bounded observed count
(limit + 1), `complete` is false, and the full-input digest is null. Per-key
drift includes typed values, resolver status, current heads, assertion IDs,
revision IDs, source IDs, and any withdrawal detail that was retained.

This mode is an exact applicability check for supplied snapshot settings, not
a generic JSON/text diff. Its factual capability locator is inert. It does not
authenticate the snapshot or establish truth, provenance, ledger freshness or
completeness, permission, safety, or task completion. Input values remain data
and source IDs remain opaque; neither is executed or dereferenced.

## What `READY` means

A Threadmark is `READY` only when every required key has one JSON-type-equal
live value across every supplied current head. It means consistency inside
the supplied ledger only. It does **not** establish factual truth, authenticated
provenance, ledger freshness or completeness, permission, safety, task
completion, intelligence, adoption, or savings.

Source IDs and labels remain opaque, unverified data. Ledger values are never
executed and sources are never dereferenced. Missing, withdrawn, or conflicting
required keys produce explicit blockers. If complete required detail cannot fit
an output bound, the command returns `DETAIL_REQUIRED` rather than truncating a
success result.

## Input and examples

[`schema.json`](schema.json) describes the accepted resolver ledger and result
shapes.
[`examples/scenarios.json`](examples/scenarios.json) contains synthetic
software-workflow examples and counterexamples for conflicts, withdrawals,
missing keys, typed equality, and size limits, plus a separately authored
fictional snapshot applicability example. These fixtures do not verify the
truth of their assertions or demonstrate general product benefit.

## Focused checks

```text
python3 -B -S tests/test_yumi_continuity.py -q
python3 -B -S tests/test_threadmark_handoff.py -q
python3 -B -S tests/test_threadmark_verify.py -q
python3 -B -S tests/test_snapshot_check.py -q
python3.12 -B tests/test_origin_receipt.py -v
```

On Windows, use `py -3.12` in place of `python3.12` for the origin-receipt
test. The included tests are synthetic contract checks. See
[`manifest.json`](manifest.json) for the exact artifact inventory, accepted
public origins, limits, and SHA-256 digests. The README digest is frozen
externally because this file is part of the described bundle.

## License

MIT. See [`LICENSE`](LICENSE).



## Cost-aware diagnostic decision trees — Yumi Probe Planner

For a small fault-isolation or troubleshooting model, calculate which check to run next and the contingent checks after each result. This independent utility minimizes **expected total check cost**, not just the cost of the next check, over the complete deterministic outcomes you supply.

- [Use the read-only JSON API](https://noqt.no-qt.chatgpt.site/api/yumi/probe-plan) — no account, API key, connected app or installation.
- [Model format, example and limits](https://noqt.no-qt.chatgpt.site/yumi/probe-planner).
- [Dependency-free JavaScript source](probe-planner/planner.mjs) and [focused tests](probe-planner/planner.test.mjs), under this repository's MIT license.

The API supports a descriptor GET, a URL-encoded model GET, and an application/json model POST. For example:

```sh
curl https://noqt.no-qt.chatgpt.site/api/yumi/probe-plan \
  -H 'Content-Type: application/json' \
  --data '{"hypotheses":[{"id":"network","weight":2},{"id":"storage","weight":1}],"probes":[{"id":"ping","cost":1,"outcomes":{"network":"timeout","storage":"ok"}}]}'
```

The same calculation can run locally after you inspect the source:

```js
import { planDiagnostic } from "./probe-planner/planner.mjs";
const plan = planDiagnostic({
  hypotheses: [{ id: "network", weight: 2 }, { id: "storage", weight: 1 }],
  probes: [{ id: "ping", cost: 1, outcomes: { network: "timeout", storage: "ok" } }]
});
console.log(plan.expectedCostNumerator, plan.expectedCostDenominator, plan.policy);
```

Inputs have 1–12 hypotheses and 0–12 probes. Hypothesis weights and probe costs are positive integers up to 1,000,000. IDs and outcome labels are 1–64 UTF-16 code units. Every probe must specify an outcome for every hypothesis. The HTTP body/decoded model limit is 65,536 UTF-8 bytes; GET also has hosting URL limits, so use POST for larger models.

The exact integer numerator/denominator fields are authoritative; decimal fields are approximate renderings. Equal-cost ties follow input order. Hypotheses with identical complete outcome vectors are explicitly reported as undistinguishable, not falsely resolved.

This is exact optimization of a caller-supplied finite deterministic model, **not** inference of real-world fault probabilities, truth, timing or costs. It does not support noisy/stochastic outcomes. This isolated, independently authored calculation does not expose Yumi's private assistant, core or memory. The application code does not persist or log submitted models, add tracking, call a model or make outbound calls; the hosting provider may process ordinary request metadata. Submit only public, non-secret models. Publication and controlled use are not evidence of independent adoption.


## Batch PyPI wheel metadata — Yumi Wheel Evidence

Published-wheel evidence, not a compatibility checker. For browser- or runtime-constrained release-engineering agents, one bounded request collects provider-reported evidence for several explicit public PyPI package versions against a global caller-supplied wheel-tag set, without installing or downloading package artifacts.

- [Anonymous read-only JSON API and descriptor](https://noqt.no-qt.chatgpt.site/api/yumi/wheel-evidence), with a URL-encoded `request` GET or an `application/json` POST.
- [Request format, limits, results and scope](https://noqt.no-qt.chatgpt.site/yumi/wheel-evidence).
- Native MCP endpoint: `https://noqt.no-qt.chatgpt.site/api/yumi/mcp` — anonymous, stateless JSON-RPC POST; no account, session or SSE.
- MCP tool: `pypi_wheel_evidence`, using the same `releases` and `tags` request arguments and provider-reported metadata limitations below.
- [Independent JavaScript collector](wheel-evidence/wheel-evidence.mjs) and [focused mocked tests](wheel-evidence/wheel-evidence.test.mjs), under this repository's MIT license. The collector has no package dependencies; it requires standard fetch, Web Streams, AbortController and text-encoding APIs.

Hosted tools checked 18 October 2026 UTC: the same MCP endpoint's `tools/list` returns `pypi_wheel_evidence` and `network_set_evidence`. The [network-set guide](https://noqt.no-qt.chatgpt.site/yumi/network-sets) covers exact mathematical IPv4/IPv6 before/after coverage differences, canonical CIDRs, address counts and witnesses. Send only public, non-secret inputs; this is not routing, reachability or security-policy advice. This newer hosted network tool is not included in the pinned source snapshot below.

The exact request keys are `releases` and `tags`. Each release has exactly `project` and `version`; the tags are uncompressed interpreter-ABI-platform triples shared by the batch. Limits are 1–8 explicit releases, 1–256 tags, 65,536 decoded input bytes over HTTP, two simultaneous upstream metadata requests, an eight-second deadline through each release response body, and 2 MiB decoded release JSON per item. Versions use conservative safe grammar and literal provider equality; full PEP 440 alias resolution is not implemented.

Results identify the collector as `Yumi Wheel Evidence` and the underlying fact source separately as each release-specific PyPI JSON URL, with observation time and provider serial. Matching wheel records include filename, intersecting caller tags, PyPI-reported SHA-256, size, yanked state/reason, Requires-Python, upload time and an inert provider-reported artifact URL. At most 50 matches per item are returned in filename order, with the exact match total and an explicit truncation flag. Redirects, retries and artifact fetches are not performed. Provider failures remain distinct from `NO_MATCHING_WHEEL`.

A match is only filename-tag intersection. No-match is not incompatibility; incomplete caller tags may omit usable wheels. The collector does not resolve dependencies, evaluate Requires-Python, build, install, establish compatibility or installability, verify artifact digests, or establish security, authenticity, origin or artifact availability. PyPI metadata can change and need not equal uploaded artifact contents. For small checks where Python is available, direct [PyPI JSON](https://docs.pypi.org/api/json/) plus [packaging](https://packaging.pypa.io/en/stable/tags.html) is the preferred native path.

Only public, non-secret requests belong on this anonymous endpoint. Application code does not persist or log requests, add tracking, use a model or expose Yumi's private assistant, core, memory or user data. The hosting provider may process ordinary request metadata, including GET URLs. This is an isolated utility, not a novel matching algorithm or evidence of independent discovery, adoption or superiority.


## Hosted source snapshot

This snapshot adds the HTTP route-handler sources at [`app/api/yumi/mcp/route.ts`](app/api/yumi/mcp/route.ts) and [`app/api/yumi/wheel-evidence/route.ts`](app/api/yumi/wheel-evidence/route.ts). They are Fetch API-style TypeScript framework route handlers, not a standalone listener or a complete deployment bundle. The MCP handler calls the local wheel-evidence HTTP handler directly; it is not an external proxy. The handlers assume the hosting framework/runtime supplies `Request`, `Response`, `Headers`, readable request streams, `TextDecoder`/`TextEncoder`, and the framework's route-handler execution model.

The fixed public URLs are unchanged. The MCP handler accepts only the fixed `https://noqt.no-qt.chatgpt.site` `Origin` or no `Origin`; the separate wheel-evidence HTTP API retains its documented public CORS behavior. Anonymous access does not mean arbitrary MCP browser-origin acceptance. This source snapshot is from Site commit `b70f0263a3ac788f0701eca468a34eb8877510a1`; the only source change is relocation of the HTTP collector import to `../../../../wheel-evidence/wheel-evidence.mjs`. This note does not attest to deployed bytes or provide a complete deployment package. These isolated handlers do not import or expose Yumi's private assistant, core, memory or data; the rest of the Site source is not included. All code is independently authored for an isolated public service.

## Machine-readable HTTP description

The [OpenAPI 3.0.3 definition](https://raw.githubusercontent.com/noqt/yumi-decision-ledger/main/openapi/yumi-wheel-evidence.json) describes the existing anonymous GET, POST and OPTIONS surface for public PyPI release wheel metadata. It covers the bounded request format, per-release provider results and HTTP errors; it does not describe Yumi's private assistant or a model service.

CC0-1.0 applies only to that original OpenAPI definition. The repository's existing MIT-licensed source and runtime are unchanged.
