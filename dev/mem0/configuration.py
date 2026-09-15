"""Configure only the checkout-owned OSS server through its public admin API.

No imports from Mem0 internals, source replacement, or direct database access.
"""

import argparse
import os
from collections.abc import Mapping

import httpx2

FIXTURE_URL = "http://embeddings:18081/v1"


def memory_config(environ: Mapping[str, str]) -> dict:
    config = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": "postgres",
                "dbname": "mem0",
                "user": "mem0",
                "password": "local-mem0-database",
                "collection_name": environ.get("MEM0_OSS_COLLECTION", "memories"),
                "embedding_model_dims": int(environ.get("MEM0_OSS_EMBEDDING_DIMENSIONS", "128")),
            },
        },
        "history_db_path": "/app/history/history.db",
    }
    for name, section, default_model in (("LLM", "llm", "local-unused"), ("EMBEDDING", "embedder", "local-hash-128")):
        prefix = f"MEM0_OSS_{name}_"
        config[section] = {
            "provider": "openai",
            "config": {
                "model": environ.get(prefix + "MODEL", default_model),
                "api_key": environ.get(prefix + "API_KEY", "local-fixture-key"),
                "openai_base_url": environ.get(prefix + "BASE_URL", FIXTURE_URL),
            },
        }
    # Database vector size is mandatory; the optional API parameter is not
    # supported by every OpenAI-compatible embedding endpoint.
    if environ.get("MEM0_OSS_EMBEDDING_SEND_DIMENSIONS", "false").lower() == "true":
        config["embedder"]["config"]["embedding_dims"] = config["vector_store"]["config"]["embedding_model_dims"]
    return config


def configure(client: httpx2.Client, environ: Mapping[str, str]) -> None:
    client.post("configure", json=memory_config(environ)).raise_for_status()
    client.get("memories", params={"user_id": "a13n-dev-startup-check", "top_k": 1}).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure a checkout-owned Mem0 Compose instance")
    parser.add_argument("--base-url", default="http://127.0.0.1:18888")
    args = parser.parse_args()
    try:
        with httpx2.Client(
            base_url=args.base_url.rstrip("/") + "/",
            headers={"X-API-Key": os.environ.get("MEM0_LOCAL_API_KEY", "local-mem0-api-key")},
            timeout=30,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            configure(client, os.environ)
    except httpx2.HTTPError:
        raise SystemExit(
            "Mem0 configuration failed. Check the local server logs and model settings; no reset was requested."
        ) from None
    print("Mem0 configured through its native API. Model changes do not re-embed existing records.")


if __name__ == "__main__":
    main()
