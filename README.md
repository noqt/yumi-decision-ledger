# Yumi Decision Ledger

Resume an agent task from an explicit revision ledger without silently treating
superseded, withdrawn, missing, or conflicting assertions as current.

This repository packages the same already-public Yumi continuity v1.1
capability; it is a descriptive discovery entrypoint, not a new independent
validation of the behavior. The accepted source originated in
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

Each command writes canonical JSON to stdout. Use `-` instead of a path to read
stdin. Run the code only when you are already authorized to inspect and execute
it and to read the supplied input. The `-I -B -S` flags reduce ambient Python
behavior; they are not an operating-system sandbox.

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
```

The included tests are synthetic contract checks. See [`manifest.json`](manifest.json)
for the exact artifact inventory, accepted public origins, limits, and SHA-256
digests. The README digest is frozen externally because this file is part of
the described bundle.

## License

MIT. See [`LICENSE`](LICENSE).
