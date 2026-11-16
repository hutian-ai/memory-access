# Memory Gateway

A unified, authorized memory gateway for applications and Agents. Connect to different memory backends through REST, MCP and CLI, with resource-scoped permissions, source attribution and operation receipts. The application uses stable resource IDs; adapters translate to the selected backend's namespace and API.

Formerly Memory Access; the repository is now [`hutian-ai/memory-gateway`](https://github.com/hutian-ai/memory-gateway). Existing package, CLI, Python module, environment variable and protocol identifiers remain compatible: `hutian-memory-access`, `memory-access`, `memory-access-admin`, `hutian_memory_access`, `MEMORY_ACCESS_*` and `memory-access/1`.

**Actively iterating.** Interfaces, configuration and backend adapters may change. Check this README and the protocol documentation before integrating.

The existing Scoped Memory Access implementation is now the Hindsight adapter and backward-compatible `/v1` entry point. The backend-neutral contract is exposed at `/v2`; its semantics are documented in [PROTOCOL.md](PROTOCOL.md). Backend location is configurable, so self-hosting is not a requirement of the protocol.

## Available backends

| Adapter | Write modes | Sources and deletion | Verification |
| --- | --- | --- | --- |
| Hindsight | Infer; native asynchronous operations | Native original sources; source and derivative deletion | Actual local Hindsight 1.2.0 write, observation, retrieval and source checks |
| Mem0 OSS | Infer or verbatim; SDK call completion | Gateway-retained original input; tracked native memory IDs | Actual Mem0 SDK 1.0.11 with Qdrant local persistence and configured model endpoints; write and retrieval checked |
| Managed files | Verbatim; synchronous | Explicitly managed notes, lexical retrieval, source-only deletion | Actual write and search; no LLM or embedding dependency |
| Mem0 Platform REST | Infer or verbatim; provider event observation | Gateway source and tracked native entries | Adapter implemented from official API documentation; no live account validation in this delivery |

Capabilities are reported per resource. The first three rows describe independent real storage paths, not three names routed to the same service. Retrieval quality and feature sets depend on the chosen backend. The gateway does not synchronize a Harness's private memory or change native model settings.

## Start without a memory service

Install Python 3.11+ and this package:

```sh
pip install .
mkdir -p .local
memory-access-admin --policy .local/policy.json init
memory-access-admin --policy .local/policy.json resource notes --connection notes --namespace my-notes
memory-access-admin --policy .local/policy.json issue --name my-agent --read notes --write notes --output .local/client.json
```

Create a private connections file with your managed notes location:

```json
{ "notes": { "type": "files", "path": "/absolute/private/path/notes" } }
```

Set `MEMORY_ACCESS_POLICY`, `MEMORY_CONNECTIONS_FILE`, `MEMORY_ACCESS_STATE` to private policy, connection and writable receipt locations, and `MEMORY_ACCESS_PUBLIC_URL` to `http://127.0.0.1:3332`. Run:

```sh
uvicorn hutian_memory_access.app:app --host 127.0.0.1 --port 3332
memory-access --config .local/client.json --protocol v2 resources
memory-access --config .local/client.json --protocol v2 capabilities notes
memory-access --config .local/client.json --protocol v2 remember notes --document-id note-1 --file ./note.md --version 1 --mode verbatim --receipt .local/write.json
memory-access --config .local/client.json --protocol v2 recall notes 'source attribution'
memory-access --config .local/client.json operation --receipt .local/write.json
```

The receipt identifies the protocol automatically when observing. Existing CLI callers default to v1. Keep connection files and issued credentials private; the connections example contains placeholders, not usable credentials.

## Connect another backend

Each private connection has a type and its own configuration. Create resources using `--connection` and `--namespace` (`--bank` remains an alias). Existing resource mappings cannot be redirected through this command.

- Hindsight: configure `type: hindsight`, an API URL and its private `api_key`. Current original resource mappings default to the `hindsight` connection.
- Mem0 OSS: install `pip install ".[mem0]"`, configure `type: mem0-oss` and a `config` object accepted by `Memory.from_config`. Use your own LLM/embedder configuration, vector storage and history database. The adapter pins SDK 1.0.11; its dialect differs from current Platform documentation. Telemetry is disabled by the adapter.
- Mem0 Platform: configure `type: mem0-platform`, the API origin and private `api_key`. It is separately marked experimental pending live account verification.

The Python core imports optional SDKs only for configured adapters. The Dockerfile builds a standalone Python service including the optional Mem0 dependency; it does not require a Hindsight image or installation. Hindsight itself is deployed separately when selected.

## Use through MCP or HTTP

MCP endpoint: `/mcp/`, with `Authorization: Bearer <resource-scoped-token>`. The versioned tools are `memory_capabilities`, `memory_write`, `memory_search`, `memory_sources`, `memory_source`, `memory_operation` and `memory_delete_source`. Legacy tools remain available for old Hindsight resources. HTTP request schemas are at `/docs`.

A write explicitly selects inference or raw storage. Responses distinguish acceptance, completion and unknown outcome. The same operation ID cannot be reused for different content. Original sources record URI, version, hash, submitter and evidence status. Backend credentials are not given to calling Agents.

This is a single-worker implementation with private local policy, source archives and receipts. Operators choose legal resource mappings; the application still owns account and business authorization. Cross-backend replication, automatic migration, federated ranking and a common reasoning engine are outside this release.

## Legacy Hindsight integration

The following earlier entry points remain supported. The original deployment configuration and resource identifiers are preserved.


A small REST/MCP authorization boundary for an independently deployed Hindsight service. External hosts provide stable resource identifiers and grants; this package does not create an account, project, role-management or memory-storage platform.

The backend owns documents, extraction, observations, operation IDs and persistence. This service holds its instance key and gives callers separate revocable credentials. Role-private and project-shared banks can be granted independently.

## Run

Python 3.11 or newer:

```sh
pip install .
memory-access-admin --policy /private/memory/policy.json init
memory-access-admin --policy /private/memory/policy.json resource researcher --bank role-researcher
memory-access-admin --policy /private/memory/policy.json resource project --bank project-shared
memory-access-admin --policy /private/memory/policy.json issue --name runtime-a \
  --read researcher project --write researcher --output /private/clients/runtime-a.json
```

Supply MEMORY_UPSTREAM_URL, MEMORY_UPSTREAM_API_KEY, MEMORY_ACCESS_POLICY and MEMORY_ACCESS_PUBLIC_URL through your private environment. Start a single server worker:

```sh
uvicorn hutian_memory_access.app:app --host 127.0.0.1 --port 3332
```

The Dockerfile builds this project's independent Python image. Mount the policy directory, connections file and writable protocol state when deploying it. Policies are read on every request; mount the directory, not a single policy inode, so atomic revocation is visible.

## Call it

Configure a Streamable HTTP MCP client with the access service's `/mcp/` URL and `Authorization: Bearer <scoped-token>`. Never give clients the Hindsight instance key. Tools are list_resources, remember_memory, recall_memory, read_source, get_operation and delete_source.

Agents with shell access can use the CLI instead:

```sh
memory-access --config /private/clients/runtime-a.json resources
memory-access --config /private/clients/runtime-a.json recall researcher 'What did we learn?'
memory-access --config /private/clients/runtime-a.json remember researcher \
  --document-id handoff --file ./handoff.md --version 1 --receipt /private/receipts/handoff.json
memory-access --config /private/clients/runtime-a.json operation --receipt /private/receipts/handoff.json
memory-access --config /private/clients/runtime-a.json source researcher handoff
```

The CLI writes an exclusive, private receipt before submitting. `operation` only observes. Explicit `retry` first observes the same UUID and resubmits its identical original request only after a confirmed 404; a timeout or service error never creates another UUID. Reusing a UUID returns the original operation, not acceptance of new content. Verify the source before treating an old operation as the result of a changed request.

REST exposes the same actions beneath `/v1/resources/{resource_id}`; `/docs` describes the exact schemas. Caller-supplied bank_id, provider configuration and administrator operations are not accepted. Sources carry source_uri, source_version, content_sha256, submitted_by and evidence_status. A claim of confirmed evidence needs a separate `confirm` grant; the service checks who may assert that status, not whether text is true.

Writers also need read permission to inspect their original operation and source. Writes and deletion are serialized per resource in this single-worker version; while backend work is pending, mutations return 409 with its original operation ID. Reads remain available. Inspect the original operation before continuing.

## Scope and limits

Each configured resource maps to a unique bank. Choose resources whose contents have the same visibility; do not mix a private conversation with project-visible material and expect a role name or client filter to hide it. The external host is responsible for selecting legal grants. The service validates them for every API/tool access, including existing clients after revocation.

Backend/admin UI access and the private policy directory are trusted operator capabilities. This is an API boundary, not isolation from a process that can already read the operator's filesystem. Keep backend ports private; publish only the access service over TLS when deploying across machines. Server-side token records contain SHA-256 hashes; client secrets are written only to their private files.

No automatic public release, full business authorization service or test framework is included. Local LLMs can be configured in Hindsight independently of this package. Decision models such as Jev can help screening/ranking but do not replace generative extraction/consolidation; the package does not depend on any one model vendor.


## Delivery status

Version 0.2.0 is a local source delivery, not a public package or hosted service. See [DELIVERY.md](DELIVERY.md) for actual backend checks and a share draft. Hindsight 1.2.0 and Mem0 OSS SDK 1.0.11 were used for these checks; a provider version change requires rechecking its dialect. Existing data is not automatically migrated when another connection is added.
