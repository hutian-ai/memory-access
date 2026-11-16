# Memory Gateway

- Provide a backend-neutral memory access contract with scoped authorization, source attribution and operation observation.
- Providers own extraction, inferred memory, embeddings and native persistence. Gateway source archives and receipts must remain separate from claims of native execution success.
- Support resource-specific capability discovery. Never silently change infer/verbatim semantics or invent unsupported source/deletion/reflect guarantees.
- Keep legacy Hindsight `/v1` callers compatible while `/v2`, MCP and CLI use the versioned public protocol.
- Preserve original resource identities, credentials and native operation references. Unknown external mutations are not replayed automatically.
- Keep secrets and evidence under ignored private directories; do not print model keys, connection strings or private policies.
- Follow the parent workspace's small-stage delivery and no-added-tests rules. Verify through compilation and short real backend use. Publication and remote pushes require explicit authorization.
