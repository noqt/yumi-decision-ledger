# Yumi Decision Ledger

Resume an agent task from an explicit revision ledger without silently treating
superseded, withdrawn, missing, or conflicting assertions as current.

This bundle is version 1.2.0. Its resolver and handoff-card base components are
the unchanged, already-public Yumi continuity v1.1 capability. The newly
authored freshness checker is an extension that packages and reuses those base
components; it is not independent validation of them. The accepted base source originated in
[`noqt/noqt`](https://github.com/noqt/noqt): the resolver bundle is pinned at
[`c9496a7d`](https://github.com/noqt/noqt/tree/c9496a7d599af07be6c170e3894de0140d3079b9/yumi/continuity/v1.1)
and the handoff-card addition at
[`ba2c990d`](https://github.com/noqt/noqt/tree/ba2c990de865cf78377486796690535a4a26874b/yumi/continuity/v1.1).
The intended canonical discovery entrypoint for this standalone package is
<https://github.com/noqt/yumi-decision-ledger>. Immutable code output may still
carry its earlier `noqt/noqt` documentation URL as origin metadata.

This standalone Python 3.11+ command-line tool:

- resolves current state across revision chains and branches;
- reports retractions, missing predecessors, unresolved forks, and provenance;
- emits a deterministic **Threadmark** for required task keys; and
- exports a bounded handoff card from an existing Threadmark.

It uses only the Python standard library. It has no network access, telemetry,
model dependency, output-file mode, or private Yumi service dependency.

## Run

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

Each command writes canonical JSON to stdout. Use `-` instead of a path to read
stdin. The freshness checker accepts stdin for either the card or the ledger,
but not both in the same invocation. Run the code only when you are already authorized to inspect and execute
it and to read the supplied input. The `-I -B -S` flags reduce ambient Python
behavior; they are not an operating-system sandbox.

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
authentication, freshness, completeness, permission, or safety checks.
The result's capability identifies this v1.2 checker and its canonical
discovery page; the separately nested base capability retains the handoff
exporter's origin metadata.

## What `READY` means

A Threadmark is `READY` only when every required key has one JSON-type-equal
live value across every supplied current head. It means consistency inside the
supplied ledger only. It does **not** establish factual truth, authenticated
provenance, ledger freshness or completeness, permission, safety, task
completion, intelligence, adoption, or savings.

Source IDs and labels remain opaque, unverified data. Ledger values are never
executed and sources are never dereferenced. Missing, withdrawn, or conflicting
required keys produce explicit blockers. If complete required detail cannot fit
an output bound, the command returns `DETAIL_REQUIRED` rather than truncating a
success result.

## Input and examples

[`schema.json`](schema.json) describes the accepted ledger and result shapes.
[`examples/scenarios.json`](examples/scenarios.json) contains synthetic
software-workflow examples and counterexamples for conflicts, withdrawals,
missing keys, typed equality, and size limits. These fixtures do not verify the
truth of their assertions or demonstrate general product benefit.

## Focused checks

```text
python3 -B -S tests/test_yumi_continuity.py -q
python3 -B -S tests/test_threadmark_handoff.py -q
python3 -B -S tests/test_threadmark_verify.py -q
```

The included tests are synthetic contract checks. See [`manifest.json`](manifest.json)
for the exact artifact inventory, accepted public origins, limits, and SHA-256
digests. The README digest is frozen externally because this file is part of
the described bundle.

## License

MIT. See [`LICENSE`](LICENSE).
