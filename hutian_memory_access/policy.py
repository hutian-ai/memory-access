from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    connection_id: str = Field(default="hindsight", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    description: str = ""


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    grants: dict[str, list[str]]
    expires_at: int | None = None
    disabled: bool = False


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    resources: dict[str, Resource]
    credentials: dict[str, Credential]

    @model_validator(mode="after")
    def validate_grants(self):
        if self.version != 1:
            raise ValueError("Unsupported policy version")
        if len({(r.connection_id, r.bank_id) for r in self.resources.values()}) != len(self.resources):
            raise ValueError("Each authorization resource must own a different bank")
        for resource_id in self.resources:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", resource_id):
                raise ValueError("Invalid resource identifier")
        for token_hash, credential in self.credentials.items():
            if len(token_hash) != 64 or any(c not in "0123456789abcdef" for c in token_hash):
                raise ValueError("Credentials must be keyed by SHA-256, never plaintext")
            for resource_id, permissions in credential.grants.items():
                if resource_id not in self.resources or not set(permissions) <= {"read", "write", "confirm"}:
                    raise ValueError("Invalid resource grant")
                if "write" in permissions and "read" not in permissions:
                    raise ValueError("Writers must also be able to observe their source and operation")
        return self


class PolicyStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> Policy:
        # Read on each request/tool call. Atomic replacement is supported; no stale grant cache.
        return Policy.model_validate_json(self.path.read_text())

    def authenticate(self, token: str) -> Credential | None:
        try:
            policy = self.load()
        except (OSError, ValueError):
            logging.error("Memory access policy is unavailable or invalid")
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        credential = policy.credentials.get(digest)
        if credential is None or credential.disabled:
            return None
        if credential.expires_at is not None and credential.expires_at <= time.time():
            return None
        return credential

    def authorize(self, token: str, resource_id: str, permission: str) -> tuple[Credential, Resource]:
        credential = self.authenticate(token)
        if credential is None:
            raise AccessDenied(401, "Credential is invalid, expired or revoked")
        try:
            policy = self.load()
        except (OSError, ValueError):
            raise AccessDenied(503, "Authorization policy is unavailable") from None
        # Validate the same token against this snapshot, including revocation between reads.
        current = policy.credentials.get(hashlib.sha256(token.encode()).hexdigest())
        if current is None or current.disabled or (current.expires_at is not None and current.expires_at <= time.time()):
            raise AccessDenied(401, "Credential is invalid, expired or revoked")
        if permission not in current.grants.get(resource_id, []):
            raise AccessDenied(403, "Resource or operation is not permitted")
        resource = policy.resources.get(resource_id)
        if resource is None:
            raise AccessDenied(403, "Resource or operation is not permitted")
        return current, resource


class AccessDenied(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)
