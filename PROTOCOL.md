# Memory Gateway protocol

Status: an implemented local contract for applications and backend adapters, not an industry standard. The discovery value is `memory-access/1`. HTTP `/v2` exposes this backend-neutral contract; `/v1` preserves the earlier Hindsight dialect.

## Resource and connection

A caller names an opaque `resource_id`. The operator maps it to a connection and a native namespace. The caller cannot choose a backend URL, credential, bank, user ID, model configuration or filesystem path in a request. A connection can use a hosted service or a locally deployed backend. Location does not change the public contract.

Every operation checks the current credential, its expiry/revocation and resource grants. Providers receive the authorized native namespace from the gateway. Read-by-id and delete must check that same namespace; a retrieval filter supplied by a caller is never an authorization grant.

One resource uses one connection. The contract does not silently replicate, fan out queries or migrate data when an operator changes providers. Existing resources cannot be rebound through the admin resource command. Explicit export/import and multi-backend query composition are future capabilities.

## Discovery and operations

| Operation | HTTP | MCP | Meaning |
| --- | --- | --- | --- |
| Discover | `GET /v2/resources` | existing `list_resources` plus `memory_capabilities` | Authorized resources and supported semantics |
| Capabilities | `GET /v2/resources/{resource}/capabilities` | `memory_capabilities` | Provider adapter capabilities; unsupported features remain unsupported |
| Write | `POST /v2/resources/{resource}/memories` | `memory_write` | Submit sourced content with explicit `infer` or `verbatim` mode |
| Search | `POST /v2/resources/{resource}/search` | `memory_search` | Query one resource; ranked items, text, attribution and native scores |
| Sources | `GET /v2/resources/{resource}/sources` | `memory_sources` | List submitted source references; does not list inferred memory as original text |
| Source | `GET /v2/resources/{resource}/sources/{id}` | `memory_source` | Read original submitted material; identifies backend or gateway as its keeper |
| Observe | `GET /v2/resources/{resource}/operations/{uuid}` | `memory_operation` | Observe the original write; never starts another native mutation |
| Delete | `DELETE /v2/resources/{resource}/sources/{id}` | `memory_delete_source` | Explicit removal within the backend's declared scope |

MCP is the tool discovery/call transport. These operation names, schemas and semantics are the memory-specific layer. REST, MCP and CLI use the same authorization and provider methods.

## Write semantics

Required fields are `operation_id`, `source_id`, `content`, `mode`, `source_uri` and `source_version`. `evidence_status` defaults to `reported`; `confirmed` additionally requires the caller's confirm grant. This records who can make an evidence claim, not proof that the content is true.

`infer` permits the selected backend to extract and consolidate memories. A write may yield zero, one or multiple memory items. `verbatim` requests raw storage. The adapter rejects a mode it does not support; it must not turn raw storage into extraction or fabricate extraction for a notes backend.

The gateway durably records input identity before calling the provider. Reusing the same operation ID and identical input returns the original operation. Changed input with the same ID returns 409. Connection failure leaves the write unconfirmed; a provider-generated ID lost with its response cannot be recovered by inventing another request. The gateway receipt does not guarantee exactly-once external effects.

States are `accepted`, `processing`, `completed`, `failed` and `unknown`. Synchronous providers can return completed immediately. Completion means the backend confirmed processing; search index visibility is separately reported as `not_confirmed` until a real query proves it. A provider response containing no extracted memories is a valid empty result, not an invented success count.

## Attribution, retrieval and deletion

Search returns resource-qualified items and source IDs. IDs and scores remain meaningful within their provider/resource; equal scores from different providers do not imply equal relevance. Missing attribution is represented by an empty source list, not a fabricated original document.

`read_source` is `native` or `gateway`. The Mem0 adapters retain submitted original material in the gateway because a generated memory is not the same document. This source archive and operation receipts are coordination data; extraction, embeddings and inferred-memory storage stay in Mem0.

`delete_source` declares `source_and_derivatives`, `tracked_entries` or `source_only`. In particular, removing a notes file and removing derived graph memories have different effects. Tracked-entry deletion only operates on IDs obtained from the original accepted write and rechecks native namespace, source and version before deleting any tracked entry. Gateway original-content copies in source and write records are cleared after confirmed deletion; immutable operation fingerprints and identifiers remain. It does not promise removal from backups, third-party exports or previously read copies.

## Adapter obligations

- Declare supported write modes, retrieval, source access, deletion scope and operation observation.
- Preserve resource isolation for writes, search, read-by-id and deletion; do not accept caller-selected native scope overrides.
- Preserve native asynchronous operation IDs when available, and return unknown when an outcome cannot be established.
- Expose optional reflection, history, temporal reasoning or other provider features only through explicit versioned extensions. This release does not expose a generic reflect operation.
- Keep provider dialect/version explicit. Mem0 OSS SDK and Mem0 Platform REST are separate adapters, even though they share a brand.
- Keep secrets and provider exception bodies out of public results. Avoid writing caller content into generic diagnostics.

OpenAPI at `/docs` and the MCP tool schemas provide executable request definitions. No new network transport, account system or model reasoning engine is required.
