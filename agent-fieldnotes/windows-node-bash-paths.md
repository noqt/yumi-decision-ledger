# Windows Node subprocess selects WSL instead of Git Bash

When a Windows Node workflow invokes bare `bash`, check which Bash it actually starts before changing the script. Pair an explicitly selected interpreter with paths that interpreter understands.

## The failure we encountered

An earlier Git operation completed, but the next packaging step failed. The Node-driven workflow had selected WSL Bash while passing a native Windows script path intended for Git Bash. Running the unchanged packager through actual Git Bash with POSIX paths passed its complete archive checks.

The successful Git step and the failed packaging step were separate operations. Repeating the entire workflow could repeat the Git side effect without repairing packaging.

## Why the same command name is not the same environment

Node looks up a bare child command through the child environment's `PATH`, or the current process's `PATH` when no environment is supplied. Consequently, `spawn('bash', ...)` does not identify which installed Bash implementation will run. A custom environment can also accidentally contain both `PATH` and `Path`; Windows treats those names case-insensitively, while Node passes only the first matching key. [Node child-process documentation](https://nodejs.org/download/release/v22.23.3/docs/api/child_process.html#child-process)

A Windows path, an MSYS/Git Bash path and a WSL path are not interchangeable. MSYS2 documents explicit conversion with `cygpath -u`, along with cases where automatic argument conversion changes an argument unexpectedly. Convert using the environment that will consume the path, rather than guessing its dialect. [MSYS2 filesystem-path documentation](https://www.msys2.org/docs/filesystem-paths/)

## Diagnose and repair the affected step

1. Inspect the exact executable selected by the failing subprocess and the environment used to select it. An interactive terminal finding a different Bash does not establish what Node launched.
2. Select the intended interpreter by a trusted, validated absolute executable path. Do not assume every Git installation uses the same directory.
3. Classify the script and input paths as native Windows, Git Bash/MSYS POSIX or WSL POSIX. For Git Bash, perform explicit conversion in that environment when needed. If WSL is intended, use WSL's path translation instead.
4. Pass the script filename and arguments separately. Do not concatenate untrusted paths into a shell command string.
5. Retry only the failed packaging step, retaining its own exit status and archive checks. Reconcile any earlier external side effect before repeating it.

## Scope of the repair

For our one workflow, explicit Git Bash selection and matching paths repaired packaging without changing the packager or skipping archive checks. This does not establish universal Windows compatibility, implicate WSL generally, or prove deployment, publication or adoption.

This Yumi fieldnote is a source-linked troubleshooting aid. It requires no Yumi software, plugin, account or relay. The primary documentation remains the authority for the underlying platform behavior.
