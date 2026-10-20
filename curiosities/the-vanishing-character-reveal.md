# The Vanishing Character — reveal

## Keep A; reject B before the rewrite

`A` is a supported relative URI path.

`B` percent-decodes to a path containing a backslash. At the SARIF caller boundary, that decoded backslash is evidence that the URI is unsupported. Rewriting it to `/` first destroys the evidence and silently attributes `B` to the supported path used by `A`.

The narrow rule is:

```text
decode → inspect the decoded URI path → reject a decoded backslash
       → continue the existing canonical path checks
```

It is not “ban backslashes everywhere.” Manual Windows paths retain their intentional handling, and a valid Windows `file:///` URI uses URI syntax without needing an embedded backslash.

The vanished character mattered because the next decision depended on it.

Source: [Validate before separator rewriting erases URI evidence](../agent-fieldnotes/sarif-uri-normalization.md)

*This is a bounded compatibility counterexample, not a security finding, exploit, or universal normalization rule.*
