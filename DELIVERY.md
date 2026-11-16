# Memory Access 0.2.0 delivery

2026-11-15. Local source delivery; no remote repository, public release, package publication or announcement. The former Scoped Memory Access Hindsight entry point is retained as `/v1`; the backend-neutral protocol is implemented at `/v2` and through MCP and CLI.

## What is independently usable

- Install the Python 3.11+ package, configure private connection mappings and issue scoped credentials. No Hutian, Dock or Matrix installation is required.
- Start with managed notes without a model, embedding service or Hindsight. Add Hindsight or Mem0 connections when those engines are wanted.
- Use explicit `infer` or `verbatim` semantics, per-resource discovery, ranked retrieval, source references, original-operation observation and declared deletion scope.
- Keep engine-side extraction and native memories in their respective providers. Gateway SQLite stores coordination receipts and Mem0 original-source archives; it is not a replacement inference engine.
- Keep existing clients, bank mappings and grants. The compatibility directory in the parent workspace is a symlink; this project itself has no runtime path dependency on it.

## Actual checks

| Backend or boundary | Observed result |
| --- | --- |
| Hindsight 1.2.0 | Real infer accepted then completed; recall and native original source read; source/derivative delete followed by source 404 |
| Mem0 OSS SDK 1.0.11 | Real Qdrant-backed verbatim write and semantic search; a separate infer produced three native items; original source retained by gateway; tracked-source deletion checked |
| Managed files | Real verbatim write, lexical search, native source and source-only deletion; infer rejected with 422 |
| Stable operation | Same UUID and same content returns original; changed content with same UUID rejected with 409 |
| Scope and revocation | Ungranted resource returns 403; a separately issued temporary credential returned 200 before revocation and 401 afterward without restarting service |
| Independent package | Installed from copied source into a separate Python 3.11 environment; import came from consumer site-packages; installed CLI reported the actual Mem0 adapter |
| MCP | Initialized a real Streamable HTTP session, listed the tools and called resource capabilities successfully |
| Persistence | Replaced/restarted the independent access container; receipts, sources, original grants and Mem0 retrieval remained in persistent state |

Private inputs and receipts live only in the host workspace's ignored evidence directory. Verification records do not include model keys, instance credentials or personal memory content. No test framework, fixture suite or new CI was added.

Mem0 Platform REST is implemented from the official API dialect but remains **experimental, without live account verification**. No claim is made about equal retrieval quality, interchangeable deletion guarantees, hosted-account setup, distributed workers or automatic cross-backend migration.

One early Mem0 inference returned zero extracted entries; that is a legitimate empty extraction. A later concrete sourced input returned three. A service replacement also caused one unconfirmed HTTP response; the original operation was checked before submitting the identical UUID/body. No new UUID was invented to hide that outcome.

## Operational boundaries

Run one server worker for the local policy, receipt and resource locks. Unknown writes are not replayed automatically. Original-source deletion clears gateway source-content copies after native confirmation and retains identifiers/fingerprints needed to prevent replay; native backups, third-party exports and already-read copies are outside this operation.

For Mem0 tracked deletion, all current native namespace/source/version ownership is checked before any tracked entry is deleted. An inferred item subsequently reassigned to another source causes a conservative conflict. The operation does not claim provider-wide erasure.

Providers can be hosted or self-managed; only the actual configured deployments listed above were exercised. Put a remote gateway behind HTTPS and your application authentication; this local delivery is not a verified multi-tenant SaaS deployment.

## Share draft — unpublished

“I wanted applications to choose a memory engine without rewriting authorization and provenance every time. Memory Access now offers one resource-scoped REST/MCP/CLI contract with adapters for Hindsight, Mem0 OSS and simple managed notes. The contract preserves their different write modes, source ownership, deletion scope and operation states. The first local checks use real engines; cloud and migration capabilities remain explicitly separate.”
