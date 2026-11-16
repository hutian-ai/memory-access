"""A normal CLI for agents without native MCP; credentials stay in a private file."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4


class ClientError(Exception):
    def __init__(self, status: int | str, detail: str):
        self.status, self.detail = status, detail


def private_json(path: Path):
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ClientError("private_file_permissions", "Credential and receipt files must have mode 600")
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description="Read and write authorized memory resources")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protocol", choices=["v1", "v2"], default="v1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resources")
    capabilities = commands.add_parser("capabilities")
    capabilities.add_argument("resource")
    for name in ("source", "delete"):
        command = commands.add_parser(name)
        command.add_argument("resource")
        command.add_argument("document_id")
    recall = commands.add_parser("recall")
    recall.add_argument("resource")
    recall.add_argument("query")
    recall.add_argument("--max-tokens", type=int, default=2000)
    recall.add_argument("--limit", type=int, default=10)
    remember = commands.add_parser("remember")
    remember.add_argument("resource")
    remember.add_argument("--document-id", required=True)
    remember.add_argument("--file", type=Path, required=True)
    remember.add_argument("--source-uri")
    remember.add_argument("--version", required=True)
    remember.add_argument("--receipt", type=Path, required=True)
    remember.add_argument("--mode", choices=["infer", "verbatim"], default="infer")
    for name in ("operation", "retry"):
        command = commands.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = private_json(args.config)

        def request(method, path, body=None):
            req = Request(config["url"].rstrip("/") + path, method=method,
                          headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json"},
                          data=None if body is None else json.dumps(body, ensure_ascii=False).encode())
            try:
                with urlopen(req, timeout=35) as response:
                    return json.load(response)
            except HTTPError as error:
                try:
                    detail = json.load(error).get("detail", "Memory request rejected")
                except (ValueError, AttributeError):
                    detail = "Memory request rejected"
                raise ClientError(error.code, detail) from None
            except (URLError, TimeoutError, OSError):
                raise ClientError("response_unknown", "No confirmed response. Inspect the original receipt; do not create another write") from None

        resource = quote(getattr(args, "resource", ""), safe="")
        prefix = "/" + args.protocol + "/resources/" + resource
        if args.command == "resources":
            result = request("GET", "/" + args.protocol + "/resources")
        elif args.command == "capabilities":
            result = request("GET", "/v2/resources/" + resource + "/capabilities")
        elif args.command == "recall":
            result = request("POST", prefix + ("/search" if args.protocol == "v2" else "/recall"),
                             {"query": args.query, "limit": args.limit} if args.protocol == "v2" else {"query": args.query, "max_tokens": args.max_tokens})
        elif args.command in ("source", "delete"):
            result = request("DELETE" if args.command == "delete" else "GET",
                             prefix + ("/sources/" if args.protocol == "v2" else "/documents/") + quote(args.document_id, safe=""))
        elif args.command == "remember":
            content = args.file.read_text()
            body = {"document_id": args.document_id, "content": content,
                    "source_uri": args.source_uri or args.file.resolve().as_uri(), "source_version": args.version,
                    "operation_id": str(uuid4())}
            if args.protocol == "v2":
                body["source_id"] = body.pop("document_id")
                body["mode"] = args.mode
            receipt = {"version": 2 if args.protocol == "v2" else 1, "resource_id": args.resource, "request": body,
                       "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
            args.receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation and fsync happen before the network request.
            with args.receipt.open("x") as output:
                os.chmod(args.receipt, 0o600)
                json.dump(receipt, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            result = request("POST", prefix + "/memories", body)
        else:
            receipt = private_json(args.receipt)
            prefix = ("/v2" if receipt.get("version") == 2 else "/v1") + "/resources/" + quote(receipt["resource_id"], safe="")
            body = receipt["request"]
            if receipt.get("version") not in (1, 2) or hashlib.sha256(body["content"].encode()).hexdigest() != receipt["content_sha256"]:
                raise ClientError("invalid_receipt", "Receipt content does not match its original hash")
            try:
                result = request("GET", prefix + "/operations/" + quote(body["operation_id"], safe=""))
            except ClientError as error:
                if args.command != "retry" or error.status != 404 or receipt.get("version") == 2:
                    raise
                # Only confirmed absence allows submitting the identical original UUID/body.
                result = request("POST", prefix + "/memories", body)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except FileExistsError:
        print(json.dumps({"error": "receipt_exists", "detail": "Observe the existing receipt; never overwrite it to retry"}), file=sys.stderr)
        raise SystemExit(1)
    except ClientError as error:
        print(json.dumps({"error": error.status, "detail": error.detail}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError):
        print(json.dumps({"error": "invalid_local_input", "detail": "Check the private config, source file and receipt"}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
