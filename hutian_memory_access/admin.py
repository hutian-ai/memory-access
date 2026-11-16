"""Local operator commands. No HTTP administration surface and no plaintext server token store."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import tempfile
import time
from uuid import uuid4


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            os.fchmod(output.fileno(), 0o600)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="Manage local scoped memory credentials")
    parser.add_argument("--policy", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    add = commands.add_parser("resource")
    add.add_argument("id")
    add.add_argument("--namespace", "--bank", dest="bank", required=True)
    add.add_argument("--connection", default="hindsight")
    add.add_argument("--description", default="")
    issue = commands.add_parser("issue")
    issue.add_argument("--name", required=True)
    issue.add_argument("--read", nargs="*", default=[])
    issue.add_argument("--write", nargs="*", default=[])
    issue.add_argument("--confirm", nargs="*", default=[])
    issue.add_argument("--expires-in", type=int, default=0, help="Seconds; zero means until revoked")
    issue.add_argument("--url", default="http://127.0.0.1:3332")
    issue.add_argument("--output", type=Path, required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("credential_id")
    commands.add_parser("list")
    args = parser.parse_args()
    os.umask(0o077)
    args.policy.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.policy.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if args.command == "init":
            if not args.policy.exists():
                atomic_json(args.policy, {"version": 1, "resources": {}, "credentials": {}})
            print("Policy ready; existing grants preserved.")
            return
        policy = json.loads(args.policy.read_text())
        if args.command == "resource":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", args.id) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", args.bank):
                parser.error("Invalid resource id")
            existing = policy["resources"].get(args.id)
            if existing and (existing["bank_id"] != args.bank or existing.get("connection_id", "hindsight") != args.connection):
                parser.error("Existing resources cannot be rebound to another bank")
            if any(key != args.id and value["bank_id"] == args.bank and value.get("connection_id", "hindsight") == args.connection for key,value in policy["resources"].items()):
                parser.error("A bank may belong to only one authorization resource")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", args.connection):
                parser.error("Invalid connection id")
            policy["resources"][args.id] = {"bank_id": args.bank, "connection_id": args.connection, "description": args.description}
        elif args.command == "issue":
            if args.output.exists():
                parser.error("Credential output already exists; choose a new path")
            grants = {}
            for permission in ("read", "write", "confirm"):
                for resource in getattr(args, permission):
                    if resource not in policy["resources"]:
                        parser.error("Unknown resource: " + resource)
                    grants.setdefault(resource, []).append(permission)
            if not grants or args.expires_in < 0:
                parser.error("Provide grants and a non-negative expiry")
            if any("write" in permissions and "read" not in permissions for permissions in grants.values()):
                parser.error("Writers also need --read so they can observe the source and original operation")
            raw_token = "mem_" + secrets.token_urlsafe(32)
            credential_id = args.name + "-" + uuid4().hex[:10]
            record = {"id": credential_id, "grants": grants,
                      "expires_at": int(time.time()) + args.expires_in if args.expires_in else None,
                      "disabled": False}
            # Write the client secret first. If the policy write fails, it has no active grant.
            atomic_json(args.output, {"url": args.url.rstrip("/"), "credential_id": credential_id,
                                      "token": raw_token, "grants": grants, "expires_at": record["expires_at"]})
            policy["credentials"][hashlib.sha256(raw_token.encode()).hexdigest()] = record
            print("Credential saved to " + str(args.output) + "; token not printed.")
        elif args.command == "revoke":
            matches = [value for value in policy["credentials"].values() if value["id"] == args.credential_id]
            if not matches:
                parser.error("Unknown credential id")
            for value in matches:
                value["disabled"] = True
        elif args.command == "list":
            print(json.dumps({"resources": policy["resources"], "credentials": list(policy["credentials"].values())}, ensure_ascii=False, indent=2))
            return
        atomic_json(args.policy, policy)
        print("Policy updated; changes apply to subsequent requests without restart.")


if __name__ == "__main__":
    main()
