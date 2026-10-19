# MCP lost replies and retry decisions

A missing reply does not tell you whether a side effect happened. Keep the outcome `UNKNOWN` until authoritative operation state or a trusted retry contract resolves it.

## A simulated lost reply

This example uses ordinary non-task `tools/call` over Streamable HTTP in MCP revision **2025-11-25**. The transport details below are revision-specific.

A fictional tool appends one marker to a record. The request ID is `7`. Its SSE response stream delivers event cursor `e17`, then disconnects before a tool response arrives.

Did the marker get added? Both histories fit that observation:

- The server failed before appending the marker.
- The server appended the marker, then failed before delivering its response.

The client cannot choose between them from the missing reply alone.

## Three identifiers with different jobs

| Identifier | Purpose in this example | What it does not establish |
|---|---|---|
| JSON-RPC request ID `7` | Match a response to its request | Business-operation deduplication |
| SSE event ID `e17` | Locate a position in one response stream | Whether the side effect committed |
| Application operation key, if supported | Identify the logical operation under the tool's retry contract | Safe repetition without that contract being honored |

In revision 2025-11-25, request IDs cannot be reused by the requestor within the same session. Reusing `7` is not a deduplication technique. [Message rules](https://modelcontextprotocol.io/specification/2025-11-25/basic#messages).

Where the server supports resumability, an HTTP GET with `Last-Event-ID: e17` can recover stream messages. That is different from POSTing another tool invocation. In this revision, disconnecting does not itself signal cancellation. [2025-11-25 transport rules](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#resumability-and-redelivery).

An `idempotentHint` is descriptive metadata, not an enforcement mechanism. Its trustworthiness depends on the server; it does not replace the operation's actual retry contract. [Maintainer guidance on annotations](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/).

## Decide before repeating the operation

Identify the negotiated protocol revision and the tool's operation contract. Recover an existing response where that revision and server support it, or inspect authoritative operation state. Repeat a side-effecting call only when the trusted contract covers that repetition. If neither state nor contract resolves the ambiguity, preserve `UNKNOWN`.

**Revision boundary:** MCP 2026-07-28 makes closing a Streamable HTTP response stream a cancellation signal; do not carry the older resumption recipe into that revision. Cancellation still is not proof that an already completed side effect was rolled back. [2026-07-28 transport overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports), [cancellation and timing rules](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation).
