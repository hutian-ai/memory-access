import { z } from "zod";

export const clientConfig = z.object({
  url: z.url(), token: z.string().min(1),
  grants: z.record(z.string(), z.array(z.string())),
  protocol: z.enum(["v1", "v2"]).default("v2"),
  writeMode: z.enum(["infer", "verbatim"]).default("infer"),
});
export type ClientConfig = z.infer<typeof clientConfig>;
export const provisionerConfig = z.object({ url: z.url(), token: z.string().min(1) }).strict();
export type ProvisionerConfig = z.infer<typeof provisionerConfig>;
const metadata = z.record(z.string(), z.unknown());
const sourceSchema = z.object({
  source_id: z.string().optional(), content: z.string(),
  metadata: metadata.default({}), provided_by: z.string(),
});
const sourcesSchema = z.object({ items: z.array(z.object({
  source_id: z.string(), title: z.string().optional(), created_at: z.string().optional(),
})).default([]) });
const state = z.enum(["accepted", "processing", "completed", "failed", "unknown"]);
const operationSchema = z.looseObject({ operation_id: z.string(), status: state,
  resource_id: z.string().optional(), source_id: z.string().optional(),
  result: z.looseObject({ search_visibility: z.string().optional(), memory_ids: z.array(z.string()).optional() }).optional(),
});
const capabilitiesSchema = z.object({ protocol_version: z.string(), resource_id: z.string(), provider: z.string(),
  capabilities: z.looseObject({ write_modes: z.array(z.enum(["infer", "verbatim"])), read_source: z.string().optional(), delete_source: z.string().optional() }),
});
const searchSchema = z.object({ items: z.array(z.looseObject({
  text: z.string(), source_ids: z.array(z.string()).default([]), metadata: metadata.nullish(),
})).default([]) });
export type MemorySource = z.infer<typeof sourceSchema>;
export type MemoryOperation = z.infer<typeof operationSchema>;
export type MemoryCapabilities = z.infer<typeof capabilitiesSchema>;
export interface MemoryWrite {
  operation_id: string; source_id: string; content: string; mode: "infer" | "verbatim";
  source_uri: string; source_version: string; evidence_status: "reported" | "confirmed";
}
export class GatewayError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}
export class GatewayBusyError extends GatewayError {}

/** Transport owned by this module. No server error bodies or credentials escape in exceptions. */
export class GatewayTransport {
  private readonly origin: string;
  constructor(private readonly config: { url: string; token: string }) {
    const url = new URL(config.url);
    if (url.username || url.password || url.search || url.hash || url.pathname !== "/" ||
      !(url.protocol === "https:" || (url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))))
      throw new Error("Gateway URL must use HTTPS or loopback HTTP without embedded credentials or a path");
    this.origin = url.origin;
  }
  async request<S extends z.ZodType>(schema: S, method: string, path: string, body?: unknown): Promise<z.infer<S>> {
    let response: Response;
    try {
      response = await fetch(this.origin + path, { method, redirect: "error", cache: "no-store", signal: AbortSignal.timeout(45000),
        headers: { authorization: `Bearer ${this.config.token}`, ...(body === undefined ? {} : { "content-type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    } catch { throw new GatewayError(503, "Gateway response is unconfirmed; inspect the original operation"); }
    if (response.status === 409) throw new GatewayBusyError(409, "Original resource operation conflicts or is still unconfirmed");
    if (!response.ok) throw new GatewayError(response.status, "Gateway rejected the operation; check connection and authorization");
    try { return schema.parse(await response.json()); }
    catch { throw new GatewayError(502, "Gateway response does not match the supported protocol"); }
  }
}
const segment = (value: string) => encodeURIComponent(value);
const legacyDocument = z.looseObject({ original_text: z.string().optional(), document_metadata: metadata.nullish() });
const legacyFact = z.looseObject({ document_id: z.string().nullish(), text: z.string().optional(), metadata: metadata.nullish() });

export class MemoryGatewayClient {
  private readonly transport: GatewayTransport;
  constructor(readonly config: ClientConfig, readonly resourceId: string) {
    this.transport = new GatewayTransport(clientConfig.parse(config));
    if (!config.grants[resourceId]?.includes("read")) throw new GatewayError(403, "Resource is outside the client grant");
  }
  private path(suffix: string, protocol: "v1" | "v2" = "v2") { return `/${protocol}/resources/${segment(this.resourceId)}/${suffix}`; }
  sources() { return this.transport.request(sourcesSchema, "GET", this.path("sources")); }
  source(id: string) { return this.transport.request(sourceSchema, "GET", this.path(`sources/${segment(id)}`)); }
  write(value: MemoryWrite) { return this.transport.request(operationSchema, "POST", this.path("memories"), value); }
  operation(id: string) { return this.transport.request(operationSchema, "GET", this.path(`operations/${segment(id)}`, this.config.protocol)); }
  search(query: string, limit = 5) { return this.transport.request(searchSchema, "POST", this.path("search"), { query, limit }); }
  capabilities() { return this.transport.request(capabilitiesSchema, "GET", this.path("capabilities")); }
  deleteSource(id: string) { return this.transport.request(z.object({ status: z.literal("completed"), scope: z.string() }), "DELETE", this.path(`sources/${segment(id)}`)); }
  legacyDocuments() { return this.transport.request(z.object({ items: z.array(z.looseObject({ id: z.string().optional(), document_id: z.string().optional(), created_at: z.string().optional(), status: z.string().optional() })).optional(), documents: z.array(z.looseObject({ id: z.string().optional(), document_id: z.string().optional(), created_at: z.string().optional(), status: z.string().optional() })).optional() }), "GET", this.path("documents", "v1")); }
  legacyDocument(id: string) { return this.transport.request(legacyDocument, "GET", this.path(`documents/${segment(id)}`, "v1")); }
  legacyWrite(value: Omit<MemoryWrite, "source_id" | "mode"> & { document_id: string }) { return this.transport.request(operationSchema, "POST", this.path("memories", "v1"), value); }
  legacyRecall(query: string) { return this.transport.request(z.object({ results: z.array(legacyFact.extend({ source_fact_ids: z.array(z.string()).nullish() })).default([]), source_facts: z.record(z.string(), legacyFact).optional() }), "POST", this.path("recall", "v1"), { query, max_tokens: 1500 }); }
}

/** Trusted host only. The server fixes provider, namespace rules and allowed grants. */
export class MemoryGatewayAdminClient {
  private readonly transport: GatewayTransport;
  constructor(config: ProvisionerConfig) { this.transport = new GatewayTransport(provisionerConfig.parse(config)); }
  provision(resourceId: string) { return this.transport.request(clientConfig, "PUT", `/admin/v1/resources/${segment(resourceId)}`); }
}
