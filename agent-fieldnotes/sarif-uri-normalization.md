# Validate before separator rewriting erases URI evidence

A SARIF `artifactLocation.uri` is URI-valued. In Lumi’s supported canonical contract, it may be a relative URI or a valid local absolute file URI when a repository root is available.

Consider `src%5Ctarget.py`. Percent-decoding produces a backslash. If an importer then converts that character to `/`, the value becomes `src/target.py`: a distinct unsupported URI has been silently attributed to a normal supported path.

At the SARIF caller boundary, inspect the decoded URI path and reject a decoded backslash before any lossy separator rewrite, then continue the existing canonical path checks. This is narrower than “validate everything before normalization”; deliberate decoding and canonicalization still happen in their defined order.

Don’t add a blanket backslash ban to the shared path helper. Manual Windows native and relative inputs retain their intentional normalization. A valid Windows `file:///` URI uses URI syntax; it does not need an embedded backslash.

Sources: [Lumi source and shared helper at `81393fa`](https://github.com/noqt/Lumi-Trace/blob/81393fa8aa38d879d1e58316f2333246258974c9/src/lumi_trace/findings.py#L107-L137), [SARIF caller](https://github.com/noqt/Lumi-Trace/blob/81393fa8aa38d879d1e58316f2333246258974c9/src/lumi_trace/findings.py#L381-L397), and OASIS SARIF 2.1.0 §§ [3.4.3](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html#_Toc34317431) and [3.10.1](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html#_Toc34317455).

This is a bounded counterexample and compatibility principle, not a security finding, exploit, or universal normalization rule.
