"""Opt-in host provisioning. A host never chooses backend URLs, namespaces or grants."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .admin import atomic_json
from .policy import AccessDenied, Policy


def mount_management(app, store):
    bearer = HTTPBearer(auto_error=False)

    @app.put("/admin/v1/resources/{resource_id}")
    def provision(resource_id: str, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        config_path = os.environ.get("MEMORY_ADMIN_CONFIG")
        if not config_path:
            raise AccessDenied(404, "Host provisioning is not enabled")
        if not credentials:
            raise AccessDenied(401, "Host credential required")
        try:
            config = json.loads(Path(config_path).read_text())
            digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
            issuer = next((v for v in config["issuers"] if hmac.compare_digest(v["token_sha256"], digest)), None)
        except Exception:
            raise AccessDenied(503, "Host provisioning configuration is unavailable") from None
        if not issuer:
            raise AccessDenied(403, "Host is not permitted")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", resource_id) or not re.fullmatch(issuer["resource_pattern"], resource_id):
            raise AccessDenied(403, "Resource is outside the host provisioner scope")
        namespace = issuer.get("namespace_prefix", "") + resource_id
        binding = {"bank_id": namespace, "connection_id": issuer["connection"], "description": ""}
        permissions = issuer.get("permissions", ["read", "write"])
        if not set(permissions) <= {"read", "write"} or "read" not in permissions:
            raise AccessDenied(503, "Invalid host provisioner grants")
        # Derivation makes retries return the same credential without storing plaintext tokens.
        token = "mem_" + hmac.new(issuer["secret"].encode(), (issuer["id"] + ":" + resource_id).encode(), hashlib.sha256).hexdigest()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        credential = {"id": issuer["id"] + "-" + hashlib.sha256(resource_id.encode()).hexdigest()[:24],
                      "grants": {resource_id: permissions}, "disabled": False, "expires_at": None}
        with store.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            policy = store.load().model_dump()
            current = policy["resources"].get(resource_id)
            if current and (current["bank_id"] != namespace or current["connection_id"] != binding["connection_id"]):
                raise AccessDenied(409, "Existing resource cannot be rebound")
            existing = policy["credentials"].get(token_hash)
            if existing and existing != credential:
                raise AccessDenied(409, "Existing host credential was changed or revoked; operator action is required")
            if not current:
                policy["resources"][resource_id] = binding
            policy["credentials"][token_hash] = credential
            try:
                validated = Policy.model_validate(policy)
            except ValueError:
                raise AccessDenied(409, "Resource namespace is already owned or invalid") from None
            atomic_json(store.path, validated.model_dump())
        return {"url": os.environ.get("MEMORY_ACCESS_PUBLIC_URL", "http://127.0.0.1:3332"),
                "token": token, "credential_id": credential["id"], "grants": credential["grants"],
                "protocol": "v2", "writeMode": issuer.get("write_mode", "infer")}
