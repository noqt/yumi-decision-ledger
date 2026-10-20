# The Vanishing Character

*An illustrative, source-grounded puzzle. It is not an observed model choice or a live-service result.*

Two SARIF `artifactLocation.uri` values arrive:

```text
A  src/target.py
B  src%5Ctarget.py
```

The importer percent-decodes them:

```text
A  src/target.py
B  src\target.py
```

Then it rewrites `\` as `/`:

```text
A  src/target.py
B  src/target.py
```

A character disappeared. The two inputs now look identical.

## What may the SARIF importer defend?

- **1 — Keep both.** They now name the same path.
- **2 — Reject both.** Any separator is ambiguous.
- **3 — Keep A; reject B before rewriting erases the distinction.**
- **4 — Let the operating system decide after rewriting.**
- **? — I’m uncertain.**

Choosing is optional. If you want the missing distinction, open the reveal.

→ [Reveal what disappeared](the-vanishing-character-reveal.md)
