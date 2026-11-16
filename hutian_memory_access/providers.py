"""Memory backend adapters. No product roles, model configuration or account management."""
from __future__ import annotations

import hashlib
import json
import asyncio
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from .policy import AccessDenied


class HTTPProvider:
    def __init__(self, config: dict):
        self.url = config["url"].rstrip("/")
        self.key = config["api_key"]

    async def request(self, method: str, path: str, body=None, *, missing=False):
        scheme = "Token" if isinstance(self, Mem0Provider) else "Bearer"
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                r = await client.request(method, self.url + path, json=body, headers={"Authorization": f"{scheme} {self.key}"})
        except httpx.RequestError:
            raise AccessDenied(503, "Backend response unknown; inspect the same operation") from None
        if missing and r.status_code == 404:
            return None
        if not r.is_success:
            raise AccessDenied(r.status_code if r.status_code in (404, 409, 422, 429) else 502, "Memory backend rejected the operation")
        return r.json() if r.content else {}


class HindsightProvider(HTTPProvider):
    capabilities = {"write_modes": ["infer"], "search": True, "read_source": "native", "delete_source": "source_and_derivatives", "operations": "native", "reflect": False}

    def bank(self, namespace):
        return "/v1/default/banks/" + quote(namespace, safe="")

    async def before_write(self, namespace):
        for status in ("pending", "processing"):
            raw = await self.request("GET", self.bank(namespace) + f"/operations?status={status}&limit=1", missing=True)
            if raw and raw.get("operations"):
                raise AccessDenied(409, "Backend resource still has work in progress")

    async def write(self, namespace, data):
        return await self.request("POST", self.bank(namespace) + "/memories", {"items": [{"document_id": data["source_id"], "content": data["content"], "metadata": data["metadata"]}], "async": True, "operation_id": data["operation_id"]})

    async def operation(self, namespace, operation_id):
        return await self.request("GET", self.bank(namespace) + "/operations/" + quote(operation_id, safe=""), missing=True)

    async def search(self, namespace, query, limit):
        raw = await self.request("POST", self.bank(namespace) + "/memories/recall", {"query": query, "max_tokens": 4000, "budget": "low", "include": {"source_facts": {"max_tokens": 4000}}})
        items = []
        for row in raw.get("results", [])[:limit]:
            source_ids = [row["document_id"]] if row.get("document_id") else list(dict.fromkeys(
                raw.get("source_facts", {}).get(f, {}).get("document_id") for f in row.get("source_fact_ids", []) if raw.get("source_facts", {}).get(f, {}).get("document_id")))
            items.append({"id": str(row.get("id", "")), "text": row.get("text", ""), "source_ids": source_ids,
                          "metadata": row.get("metadata", {}), "score": row.get("score"), "score_kind": "provider_native"})
        return items

    async def source(self, namespace, source_id):
        raw = await self.request("GET", self.bank(namespace) + "/documents/" + quote(source_id, safe=""))
        return {"source_id": source_id, "content": raw.get("original_text"), "metadata": raw.get("document_metadata", {}), "provided_by": "backend"}

    async def sources(self, namespace):
        raw = await self.request("GET", self.bank(namespace) + "/documents?limit=100")
        return [{"source_id": row.get("id", row.get("document_id", "")), "created_at": row.get("created_at", "")} for row in raw.get("items", raw.get("documents", []))]

    async def delete(self, namespace, source_id):
        for status in ("pending", "processing"):
            raw = await self.request("GET", self.bank(namespace) + f"/operations?status={status}&limit=1", missing=True)
            if raw and raw.get("operations"):
                raise AccessDenied(409, "Backend resource still has work in progress")
        return await self.request("DELETE", self.bank(namespace) + "/documents/" + quote(source_id, safe=""))


class Mem0Provider(HTTPProvider):
    """Mem0 Platform REST dialect. OSS servers require their own declared dialect."""
    capabilities = {"write_modes": ["infer", "verbatim"], "search": True, "read_source": "gateway", "delete_source": "tracked_entries", "operations": "native", "reflect": False}

    async def write(self, namespace, data):
        return await self.request("POST", "/v1/memories/", {"messages": [{"role": "user", "content": data["content"]}],
            "user_id": namespace, "infer": data["mode"] == "infer", "metadata": {**data["metadata"], "source_id": data["source_id"]}})

    async def operation(self, namespace, operation_id):
        return await self.request("GET", "/v1/event/" + quote(operation_id, safe="") + "/", missing=True)

    async def search(self, namespace, query, limit):
        raw = await self.request("POST", "/v2/memories/search/", {"query": query, "filters": {"user_id": namespace}, "top_k": limit})
        rows = raw if isinstance(raw, list) else raw.get("results", [])
        return [{"id": str(r.get("id", "")), "text": r.get("memory", ""), "source_ids": [r["metadata"]["source_id"]] if r.get("metadata", {}).get("source_id") else [],
                 "metadata": r.get("metadata", {}), "score": r.get("score"), "score_kind": "provider_native"} for r in rows[:limit]]

    @staticmethod
    def validate_owner(original, namespace, expected):
        metadata = original.get("metadata") or {}
        if original.get("user_id") != namespace or any(metadata.get(k) != expected.get(k) for k in ("source_id", "source_version", "content_sha256")):
            raise AccessDenied(409, "Backend memory ownership or source version changed; no unconfirmed entries were deleted")

    async def delete_entries(self, namespace, entries):
        pending = []
        for memory_id, expected in entries.items():
            original = await self.request("GET", "/v1/memories/" + quote(memory_id, safe="") + "/", missing=True)
            if original:
                self.validate_owner(original, namespace, expected)
                pending.append(memory_id)
        for memory_id in pending:
            await self.request("DELETE", "/v1/memories/" + quote(memory_id, safe="") + "/")


class FilesProvider:
    """Explicitly managed, verbatim notes. Simple lexical retrieval; no inferred memory claims."""
    capabilities = {"write_modes": ["verbatim"], "search": True, "read_source": "native", "delete_source": "source_only", "operations": "synchronous", "reflect": False}

    def __init__(self, config: dict):
        self.root = Path(config["path"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def folder(self, namespace):
        directory = self.root / hashlib.sha256(namespace.encode()).hexdigest()
        directory.mkdir(exist_ok=True, mode=0o700)
        return directory

    def path(self, namespace, source_id):
        return self.folder(namespace) / (hashlib.sha256(source_id.encode()).hexdigest() + ".json")

    async def write(self, namespace, data):
        from .admin import atomic_json
        atomic_json(self.path(namespace, data["source_id"]), data)
        return {"status": "completed", "operation_id": data["operation_id"], "ids": [data["source_id"]]}

    async def search(self, namespace, query, limit):
        import re
        terms = [t.casefold() for t in re.findall(r"\w+", query)]
        rows = []
        for file in self.folder(namespace).glob("*.json"):
            row = json.loads(file.read_text())
            text = row["content"].casefold()
            matches = sum(text.count(term) for term in terms)
            if matches:
                rows.append({"id": row["source_id"], "text": row["content"], "source_ids": [row["source_id"]], "metadata": row["metadata"], "score": matches, "score_kind": "lexical_count"})
        return sorted(rows, key=lambda row: row["score"], reverse=True)[:limit]

    async def source(self, namespace, source_id):
        path = self.path(namespace, source_id)
        if not path.exists():
            raise AccessDenied(404, "Source not found")
        row = json.loads(path.read_text())
        return {"source_id": source_id, "content": row["content"], "metadata": row["metadata"], "provided_by": "backend"}

    async def delete(self, namespace, source_id):
        self.path(namespace, source_id).unlink(missing_ok=True)
        return {"status": "completed"}


_mem0_instances = {}


class Mem0OSSProvider(Mem0Provider):
    """Pinned optional SDK adapter. Its namespace is selected only by trusted resource policy."""
    capabilities = {**Mem0Provider.capabilities, "operations": "synchronous"}

    def __init__(self, config: dict):
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if key not in _mem0_instances:
            os.environ.setdefault("MEM0_DIR", str(Path(os.environ.get("MEMORY_ACCESS_STATE", "/run/memory-state")) / "mem0-config"))
            os.environ["MEM0_TELEMETRY"] = "false"
            try:
                from mem0 import Memory
            except ImportError:
                raise AccessDenied(503, "Install the optional mem0 backend dependency") from None
            _mem0_instances[key] = Memory.from_config(config["config"])
        self.memory = _mem0_instances[key]

    async def write(self, namespace, data):
        result = await asyncio.to_thread(self.memory.add, [{"role": "user", "content": data["content"]}], user_id=namespace,
            infer=data["mode"] == "infer", metadata={**data["metadata"], "source_id": data["source_id"]})
        return {**result, "status": "completed"}

    async def search(self, namespace, query, limit):
        raw = await asyncio.to_thread(self.memory.search, query, user_id=namespace, limit=limit)
        rows = raw if isinstance(raw, list) else raw.get("results", [])
        return [{"id": str(r.get("id", "")), "text": r.get("memory", ""), "source_ids": [r["metadata"]["source_id"]] if r.get("metadata", {}).get("source_id") else [],
                 "metadata": r.get("metadata", {}), "score": r.get("score"), "score_kind": "provider_native"} for r in rows[:limit]]

    async def delete_entries(self, namespace, entries):
        pending = []
        for memory_id, expected in entries.items():
            original = await asyncio.to_thread(self.memory.get, memory_id)
            if original:
                self.validate_owner(original, namespace, expected)
                pending.append(memory_id)
        for memory_id in pending:
            await asyncio.to_thread(self.memory.delete, memory_id)


PROVIDERS = {"hindsight": HindsightProvider, "mem0-platform": Mem0Provider, "mem0-oss": Mem0OSSProvider, "files": FilesProvider}
