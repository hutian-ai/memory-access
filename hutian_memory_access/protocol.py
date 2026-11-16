"""Versioned memory access contract, independent of backend bank/document dialects."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from .policy import AccessDenied
from .providers import PROVIDERS, HindsightProvider, Mem0Provider


class Write(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    content: str = Field(min_length=1, max_length=200000)
    mode: Literal["infer", "verbatim"]
    source_uri: str = Field(min_length=1, max_length=2000)
    source_version: str = Field(min_length=1, max_length=200)
    evidence_status: Literal["reported", "confirmed"] = "reported"


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)


class Gateway:
    def __init__(self, store):
        self.store = store
        self.state = Path(os.environ.get("MEMORY_ACCESS_STATE", "/run/memory-state"))
        self.locks: dict[str, asyncio.Lock] = {}

    @contextmanager
    def database(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.state / "receipts.sqlite3")
        os.chmod(self.state / "receipts.sqlite3", 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS operations(resource TEXT, id TEXT, fingerprint TEXT, payload TEXT, native_id TEXT, status TEXT, result TEXT, PRIMARY KEY(resource,id))")
        connection.execute("CREATE TABLE IF NOT EXISTS sources(resource TEXT, id TEXT, payload TEXT, native_ids TEXT, PRIMARY KEY(resource,id))")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def connection(self, resource):
        path = os.environ.get("MEMORY_CONNECTIONS_FILE")
        connections = json.loads(Path(path).read_text()) if path else {"hindsight": {"type": "hindsight", "url": os.environ.get("MEMORY_UPSTREAM_URL", "http://hindsight:8888"), "api_key": os.environ.get("MEMORY_UPSTREAM_API_KEY", "")}}
        config = connections.get(resource.connection_id)
        if not config or config.get("type") not in PROVIDERS:
            raise AccessDenied(503, "Memory connection unavailable")
        try:
            return PROVIDERS[config["type"]](config), config["type"]
        except AccessDenied:
            raise
        except Exception:
            raise AccessDenied(503, "Memory connection initialization failed; inspect private operator configuration") from None

    def resource(self, token, resource_id, permission):
        credential, resource = self.store.authorize(token, resource_id, permission)
        provider, kind = self.connection(resource)
        return credential, resource, provider, kind

    def capabilities(self, token, resource_id):
        _, _, provider, kind = self.resource(token, resource_id, "read")
        return {"protocol_version": "memory-access/1", "resource_id": resource_id, "provider": kind, "capabilities": provider.capabilities}

    @staticmethod
    async def backend(operation):
        try:
            return await operation
        except AccessDenied:
            raise
        except Exception:
            raise AccessDenied(503, "Backend response is unconfirmed; inspect the original resource and operation") from None

    async def write(self, token, resource_id, value: Write):
        async with self.locks.setdefault(resource_id, asyncio.Lock()):
            return await self._write(token, resource_id, value)

    async def _write(self, token, resource_id, value: Write):
        credential, resource, provider, kind = self.resource(token, resource_id, "write")
        if value.evidence_status == "confirmed":
            self.store.authorize(token, resource_id, "confirm")
        if value.mode not in provider.capabilities["write_modes"]:
            raise AccessDenied(422, "Requested write semantics are not supported by this resource")
        payload = value.model_dump(mode="json")
        payload["metadata"] = {"source_uri": value.source_uri, "source_version": value.source_version,
            "content_sha256": hashlib.sha256(value.content.encode()).hexdigest(), "submitted_by": credential.id,
            "evidence_status": value.evidence_status, "resource_id": resource_id}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.database() as db:
            old = db.execute("SELECT * FROM operations WHERE resource=? AND id=?", (resource_id, str(value.operation_id))).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise AccessDenied(409, "Operation id already belongs to different input")
                return await self.operation(token, resource_id, str(value.operation_id))
            active = db.execute("SELECT id FROM operations WHERE resource=? AND status NOT IN ('completed','failed')", (resource_id,)).fetchall()
        for row in active:
            original = await self.operation(token, resource_id, row["id"])
            if original["status"] not in ("completed", "failed"):
                raise AccessDenied(409, "Resource has unconfirmed work; inspect original operation " + row["id"])
        if hasattr(provider, "before_write"):
            await self.backend(provider.before_write(resource.bank_id))
        with self.database() as db:
            payload["saved_at"] = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?)", (resource_id, str(value.operation_id), fingerprint, json.dumps(payload), str(value.operation_id) if kind == "hindsight" else None, "unknown", "{}"))
        # The receipt is durable before the external write. Never replay an unknown write.
        try:
            raw = await provider.write(resource.bank_id, payload)
        except AccessDenied:
            raise
        except Exception:
            raise AccessDenied(503, "Backend write is unconfirmed; inspect the original operation") from None
        native_id = raw.get("operation_id") or raw.get("event_id")
        status = self.status(raw.get("status", "completed" if not native_id else "accepted"))
        result = self.result(raw)
        with self.database() as db:
            db.execute("UPDATE operations SET native_id=?,status=?,result=? WHERE resource=? AND id=?", (native_id, status, json.dumps(result), resource_id, str(value.operation_id)))
            db.execute("INSERT INTO sources VALUES(?,?,?,?) ON CONFLICT(resource,id) DO UPDATE SET payload=excluded.payload,native_ids=excluded.native_ids", (resource_id, value.source_id, json.dumps(payload), json.dumps(result["memory_ids"])))
        return {"operation_id": str(value.operation_id), "status": status, "resource_id": resource_id, "source_id": value.source_id, "result": result}

    @staticmethod
    def status(raw):
        return {"pending": "accepted", "PENDING": "accepted", "processing": "processing", "PROCESSING": "processing", "completed": "completed", "COMPLETED": "completed", "failed": "failed", "FAILED": "failed", "accepted": "accepted", "success": "completed"}.get(raw, "unknown")

    @staticmethod
    def result(raw):
        rows = raw.get("results", raw.get("data", []))
        ids = raw.get("ids", []) or ([str(row.get("id", row.get("memory_id"))) for row in rows if isinstance(row, dict) and (row.get("id") or row.get("memory_id"))] if isinstance(rows, list) else [])
        return {"memory_ids": ids, "search_visibility": "not_confirmed"}

    async def operation(self, token, resource_id, operation_id):
        _, resource, provider, _ = self.resource(token, resource_id, "read")
        with self.database() as db:
            row = db.execute("SELECT * FROM operations WHERE resource=? AND id=?", (resource_id, operation_id)).fetchone()
        if not row:
            raise AccessDenied(404, "No operation in this resource")
        status, result = row["status"], json.loads(row["result"])
        if status not in ("completed", "failed") and row["native_id"]:
            raw = await self.backend(provider.operation(resource.bank_id, row["native_id"]))
            if raw:
                status, result = self.status(raw.get("status")), self.result(raw)
                with self.database() as db:
                    db.execute("UPDATE operations SET status=?,result=? WHERE resource=? AND id=? AND status NOT IN ('completed','failed')", (status, json.dumps(result), resource_id, operation_id))
                    if result["memory_ids"]:
                        source_id = json.loads(row["payload"])["source_id"]
                        db.execute("UPDATE sources SET native_ids=? WHERE resource=? AND id=? AND json_extract(payload,'$.operation_id')=?", (json.dumps(result["memory_ids"]), resource_id, source_id, operation_id))
                    current = db.execute("SELECT status,result FROM operations WHERE resource=? AND id=?", (resource_id, operation_id)).fetchone()
                    status, result = current["status"], json.loads(current["result"])
        return {"operation_id": operation_id, "status": status, "resource_id": resource_id, "result": result}

    async def search(self, token, resource_id, value: Search):
        _, resource, provider, kind = self.resource(token, resource_id, "read")
        items = await self.backend(provider.search(resource.bank_id, value.query, value.limit))
        # No cross-provider score blending; rank is local to the selected resource.
        return {"resource_id": resource_id, "provider": kind, "items": [{**item, "resource_id": resource_id, "rank": i + 1} for i, item in enumerate(items)]}

    async def sources(self, token, resource_id):
        _, resource, provider, _ = self.resource(token, resource_id, "read")
        if isinstance(provider, HindsightProvider):
            return {"items": await self.backend(provider.sources(resource.bank_id))}
        with self.database() as db:
            rows = db.execute("SELECT id,payload FROM sources WHERE resource=? ORDER BY rowid DESC LIMIT 100", (resource_id,)).fetchall()
        return {"items": [{"source_id": row["id"], "created_at": json.loads(row["payload"]).get("saved_at", ""),
            "title": json.loads(row["payload"])["content"].splitlines()[0].lstrip("# ")[:80]} for row in rows]}

    async def source(self, token, resource_id, source_id):
        _, resource, provider, _ = self.resource(token, resource_id, "read")
        if provider.capabilities["read_source"] == "native":
            return await self.backend(provider.source(resource.bank_id, source_id))
        with self.database() as db:
            row = db.execute("SELECT * FROM sources WHERE resource=? AND id=?", (resource_id, source_id)).fetchone()
        if not row:
            raise AccessDenied(404, "No submitted source in this resource")
        payload = json.loads(row["payload"])
        return {"source_id": source_id, "content": payload["content"], "metadata": payload["metadata"], "provided_by": "gateway"}

    async def delete(self, token, resource_id, source_id):
        async with self.locks.setdefault(resource_id, asyncio.Lock()):
            return await self._delete(token, resource_id, source_id)

    async def _delete(self, token, resource_id, source_id):
        _, resource, provider, _ = self.resource(token, resource_id, "write")
        with self.database() as db:
            active = db.execute("SELECT id,payload FROM operations WHERE resource=? AND status NOT IN ('completed','failed')", (resource_id,)).fetchall()
        for row in active:
            if json.loads(row["payload"])["source_id"] == source_id:
                current = await self.operation(token, resource_id, row["id"])
                if current["status"] not in ("completed", "failed"):
                    raise AccessDenied(409, "Source still has unconfirmed work; inspect the original operation")
        # Deletion is explicit and never uses a provider-wide delete-all endpoint.
        if isinstance(provider, Mem0Provider):
            with self.database() as db:
                row = db.execute("SELECT * FROM sources WHERE resource=? AND id=?", (resource_id, source_id)).fetchone()
            if not row:
                raise AccessDenied(404, "No tracked source in this resource")
            # A source may have multiple versions. Validate all current native
            # owners before deleting any entry, so a later inference that reused
            # an id for another source cannot be removed by this older source.
            with self.database() as db:
                writes = db.execute("SELECT payload,result FROM operations WHERE resource=? AND status='completed' ORDER BY rowid", (resource_id,)).fetchall()
            entries = {}
            for write in writes:
                payload = json.loads(write["payload"])
                if payload["source_id"] == source_id:
                    for memory_id in json.loads(write["result"]).get("memory_ids", []):
                        entries[memory_id] = {"source_id": source_id, **payload["metadata"]}
            await self.backend(provider.delete_entries(resource.bank_id, entries))
        else:
            await self.backend(provider.delete(resource.bank_id, source_id))
        with self.database() as db:
            db.execute("DELETE FROM sources WHERE resource=? AND id=?", (resource_id, source_id))
            for row in db.execute("SELECT id,payload FROM operations WHERE resource=?", (resource_id,)).fetchall():
                payload = json.loads(row["payload"])
                if payload["source_id"] == source_id:
                    payload["content"] = ""
                    payload["source_deleted"] = True
                    db.execute("UPDATE operations SET payload=? WHERE resource=? AND id=?", (json.dumps(payload), resource_id, row["id"]))
        return {"status": "completed", "scope": provider.capabilities["delete_source"]}


def mount_protocol(app, mcp, store, token_dependency, mcp_token):
    gateway = Gateway(store)

    @app.get("/v2/resources")
    async def resources(raw_token: str = Depends(token_dependency)):
        credential = store.authenticate(raw_token)
        if credential is None:
            raise AccessDenied(401, "Invalid credential")
        return {"protocol_version": "memory-access/1", "resources": [gateway.capabilities(raw_token, key) for key, permissions in credential.grants.items() if "read" in permissions]}

    @app.get("/v2/resources/{resource_id}/capabilities")
    async def capabilities(resource_id: str, raw_token: str = Depends(token_dependency)):
        return gateway.capabilities(raw_token, resource_id)

    @app.post("/v2/resources/{resource_id}/memories")
    async def write(resource_id: str, value: Write, raw_token: str = Depends(token_dependency)):
        return await gateway.write(raw_token, resource_id, value)

    @app.post("/v2/resources/{resource_id}/search")
    async def search(resource_id: str, value: Search, raw_token: str = Depends(token_dependency)):
        return await gateway.search(raw_token, resource_id, value)

    @app.get("/v2/resources/{resource_id}/sources/{source_id}")
    async def source(resource_id: str, source_id: str, raw_token: str = Depends(token_dependency)):
        return await gateway.source(raw_token, resource_id, source_id)

    @app.get("/v2/resources/{resource_id}/sources")
    async def sources(resource_id: str, raw_token: str = Depends(token_dependency)):
        return await gateway.sources(raw_token, resource_id)

    @app.delete("/v2/resources/{resource_id}/sources/{source_id}")
    async def delete(resource_id: str, source_id: str, raw_token: str = Depends(token_dependency)):
        return await gateway.delete(raw_token, resource_id, source_id)

    @app.get("/v2/resources/{resource_id}/operations/{operation_id}")
    async def operation(resource_id: str, operation_id: UUID, raw_token: str = Depends(token_dependency)):
        return await gateway.operation(raw_token, resource_id, str(operation_id))

    @mcp.tool()
    def memory_capabilities(resource_id: str) -> dict:
        """Discover the authorized memory resource's supported semantics before using it."""
        return gateway.capabilities(mcp_token(), resource_id)

    @mcp.tool()
    async def memory_write(resource_id: str, source_id: str, content: str, mode: Literal["infer", "verbatim"], source_uri: str, source_version: str, operation_id: UUID) -> dict:
        """Write with explicit extraction semantics. Observe the same operation after unknown responses."""
        return await gateway.write(mcp_token(), resource_id, Write(source_id=source_id, content=content, mode=mode, source_uri=source_uri, source_version=source_version, operation_id=operation_id))

    @mcp.tool()
    async def memory_search(resource_id: str, query: str, limit: int = 10) -> dict:
        """Search one authorized resource. Native scores are not comparable across backends."""
        return await gateway.search(mcp_token(), resource_id, Search(query=query, limit=limit))

    @mcp.tool()
    async def memory_source(resource_id: str, source_id: str) -> dict:
        """Read a source; provided_by distinguishes backend content and gateway-retained input."""
        return await gateway.source(mcp_token(), resource_id, source_id)

    @mcp.tool()
    async def memory_sources(resource_id: str) -> dict:
        """List submitted sources in this authorized resource, without memory engine assumptions."""
        return await gateway.sources(mcp_token(), resource_id)

    @mcp.tool()
    async def memory_operation(resource_id: str, operation_id: UUID) -> dict:
        """Observe the original write only. Does not retry a backend mutation."""
        return await gateway.operation(mcp_token(), resource_id, str(operation_id))

    @mcp.tool()
    async def memory_delete_source(resource_id: str, source_id: str) -> dict:
        """Explicitly delete within the backend's declared deletion scope."""
        return await gateway.delete(mcp_token(), resource_id, source_id)
