from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check a deployed noteapp-server instance.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Base URL for noteapp-server")
    parser.add_argument("--expect-repository", default="sqlite", help="Expected repository type")
    parser.add_argument("--expect-cas-strategy", default="sqlite-immediate", help="Expected CAS lock strategy")
    parser.add_argument("--expect-blob-store", help="Expected blob store type, for example s3 or oss")
    parser.add_argument("--min-schema-version", type=int, default=2, help="Minimum SQLite schema version")
    return parser.parse_args()


def fetch_json(base_url: str, path: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} returned HTTP {error.code}: {payload}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{path} request failed: {error}") from error
    try:
        parsed = json.loads(payload) if payload else {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{path} returned invalid JSON: {payload}") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{path} returned non-object JSON")
    return parsed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    args = parse_args()
    try:
        health = fetch_json(args.base_url, "/health")
        dependencies = fetch_json(args.base_url, "/health/dependencies")
        metrics = fetch_json(args.base_url, "/metrics")

        require(health.get("ok") is True, "/health is not ok")
        require(dependencies.get("ok") is True, "/health/dependencies is not ok")
        require(metrics.get("ok") is True, "/metrics is not ok")

        repository = dependencies.get("repository")
        require(isinstance(repository, dict), "repository diagnostics are missing")
        require(repository.get("type") == args.expect_repository, f"repository type is not {args.expect_repository}")
        if args.expect_repository == "sqlite":
            schema_version = repository.get("applied_schema_version")
            require(
                isinstance(schema_version, int) and schema_version >= args.min_schema_version,
                f"SQLite schema version is below {args.min_schema_version}",
            )

        cas_lock = dependencies.get("cas_lock")
        require(isinstance(cas_lock, dict), "CAS lock diagnostics are missing")
        require(cas_lock.get("strategy") == args.expect_cas_strategy, f"CAS strategy is not {args.expect_cas_strategy}")

        blob_store = dependencies.get("blob_store")
        require(isinstance(blob_store, dict), "blob store diagnostics are missing")
        require(blob_store.get("ok") is True, "blob store diagnostics are not ok")
        if args.expect_blob_store:
            require(blob_store.get("type") == args.expect_blob_store, f"blob store type is not {args.expect_blob_store}")

        for metric_name in (
            "requests_total",
            "errors_total",
            "error_rate",
            "commit_conflicts_total",
            "commit_cas_rejections_total",
            "cas_lock_wait_events_total",
            "object_storage_failures_total",
        ):
            require(metric_name in metrics, f"metric is missing: {metric_name}")
    except RuntimeError as error:
        print(f"noteapp-server deployment check failed: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "base_url": args.base_url,
                "repository": repository.get("type"),
                "schema_version": repository.get("applied_schema_version"),
                "blob_store": blob_store.get("type"),
                "cas_strategy": cas_lock.get("strategy"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
