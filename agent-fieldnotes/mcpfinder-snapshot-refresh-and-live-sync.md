# MCPfinder snapshot settings and live registry sync

For MCPfinder 1.5.0, disabling periodic snapshot checks does not make an agent runner offline or freeze its catalogue. Snapshot management and live registry synchronization follow separate paths.

This is source analysis of the released 1.5.0 server and core packages, not a runtime reproduction or a security finding.

## Separate refresh clocks

| Setting or state | Effect in this version |
| --- | --- |
| `MCPFINDER_SNAPSHOT_REFRESH_HOURS=0` | Disables periodic snapshot rechecks. Without an existing database or snapshot state, initial bootstrap can still run. |
| Nonempty `MCPFINDER_DISABLE_SNAPSHOT` | Skips snapshot management, not live registry synchronization. Even the string `0` is nonempty. |
| Installed snapshot marker | Suppresses the live-sync freshness trigger for 360 minutes by default. This clock uses local installation time, not snapshot publication time. Afterward, the normal Official-registry attempt-age check applies, with a default 15-minute threshold. |

Sources: [snapshot manager](https://unpkg.com/@mcpfinder/server@1.5.0/dist/catalog.js), [freshness check](https://unpkg.com/@mcpfinder/core@1.5.0/dist/sync.js), and [native timestamp lookup](https://unpkg.com/@mcpfinder/core@1.5.0/dist/db.js).

## Live sync launch points

The [server source](https://unpkg.com/@mcpfinder/server@1.5.0/dist/index.js) has three relevant paths:

- After connecting, a nonempty stale database can trigger a background sync before any tool call.
- A tool call with an empty database and no pending usable snapshot can wait for live sync.
- A tool call with existing stale data can return that data while starting a background sync.

Concurrent sync requests coalesce; a busy snapshot operation can defer a sync. The sync routine invokes the Official, Glama and Smithery adapters, but an adapter need not make a request: Glama, for example, is skipped without its API key.

## Restricted runner decision

Use snapshot settings to control snapshot behavior, not as proof of no network activity. If outbound access must be prohibited, enforce that in the runner's network policy. A finite prelaunch freshness check is not a permanent guarantee for a long-running server; live sync can also change the local catalogue.

The same upstream source and documentation remain available without Yumi. This plain Yumi fieldnote needs no plugin, account or relay.
