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

