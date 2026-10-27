"""Automatic account preparation. Called only by explicit managed creation."""

import hashlib
import json
import re
from importlib.resources import files

import httpx2
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.cloudflare.shared import CloudflareReference, bridge_token
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP
from pydantic import JsonValue

ASSETS = ("worker.mjs", "controller.mjs", "setup.mjs", "guest.mjs")


def sources() -> dict[str, str]:
    root = files("a13n_service.providers.environments.cloudflare")
    return {name: root.joinpath(name).read_text() for name in ASSETS}


def manifest() -> str:
    return hashlib.sha256(json.dumps(sources(), sort_keys=True).encode()).hexdigest()


def result(value: JsonValue) -> JsonValue:
    if not isinstance(value, dict) or value.get("success") is not True or "result" not in value:
        raise failure("cloudflare", "provider_response_invalid", Category.UNAVAILABLE)
    return value["result"]


async def prepare(reference: CloudflareReference) -> str:
    account = reference.backend.account_id
    script = reference.script
    token = reference.credential.api_key.get_secret_value()
    api = NativeHTTP("cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{account}/", token, 30)
    try:
        subdomain = await api.request("GET", "workers/subdomain", missing=True)
        if subdomain is None:
            # Account-wide registration is deterministic and never replaces an existing choice.
            await api.request("PUT", "workers/subdomain", body={"subdomain": "a13n-" + account})
            subdomain = await api.request("GET", "workers/subdomain")
        domain = result(subdomain)
        if not isinstance(domain, dict) or not isinstance(domain.get("subdomain"), str):
            raise failure("cloudflare", "provider_response_invalid", Category.UNAVAILABLE)
        suffix = str(domain["subdomain"])
        if not re.fullmatch(r"[a-z0-9-]{1,63}", suffix):
            raise failure("cloudflare", "provider_response_invalid", Category.UNAVAILABLE)
        endpoint = f"https://{script}.{suffix}.workers.dev"
        await reference.backend._endpoint_policy.validate(endpoint)
        tags = ["a13n-containers", "a13n-manifest:" + manifest(), "a13n-owner:" + script]
        path = f"workers/scripts/{script}"
        existing = await api.request("GET", path + "/settings", missing=True)
        if existing is not None:
            settings = result(existing)
            saved_tags = settings.get("tags") if isinstance(settings, dict) else None
            if not isinstance(saved_tags, list) or not all(tag in saved_tags for tag in tags):
                raise failure("cloudflare", "provider_deployment_conflict", Category.CONFLICT)
        else:
            source = sources()
            metadata = {
                "main_module": "worker.mjs",
                "compatibility_date": "2026-10-01",
                "tags": tags,
                "bindings": [
                    {"type": "durable_object_namespace", "name": "ENVIRONMENTS", "class_name": "Environment"},
                    {"type": "plain_text", "name": "ACCOUNT_ID", "text": account},
                    {"type": "plain_text", "name": "SCRIPT_NAME", "text": script},
                    {
                        "type": "plain_text",
                        "name": "AUTH_HASH",
                        "text": hashlib.sha256(bridge_token(script, token).encode()).hexdigest(),
                    },
                    {"type": "plain_text", "name": "GUEST_SOURCE", "text": source["guest.mjs"]},
                ],
                "migrations": {"new_tag": "v1", "new_sqlite_classes": ["Environment"]},
                "containers": [{"class_name": "Environment"}],
            }
            multipart: dict[str, tuple[str | None, str, str]] = {
                "metadata": (None, json.dumps(metadata), "application/json")
            }
            multipart.update(
                {
                    name: (name, text, "application/javascript+module")
                    for name, text in source.items()
                    if name != "guest.mjs"
                }
            )
            try:
                response = await api.client.put(path, files=multipart)
                api.check(response, mutation=True)
                result(response.json())
            except httpx2.HTTPError:
                raise failure(
                    "cloudflare",
                    "provider_bootstrap_unconfirmed",
                    Category.UNKNOWN_OUTCOME,
                    certainty=Certainty.UNKNOWN,
                ) from None
        await api.request("POST", path + "/subdomain", body={"enabled": True, "previews_enabled": False})
        bridge = NativeHTTP("cloudflare", endpoint + "/", bridge_token(script, token), 60)
        try:
            prepared = await bridge.request("POST", "setup", headers={"X-A13N-Cloudflare-Token": token})
            if not isinstance(prepared, dict) or prepared.get("phase") != "ready":
                # Submission is persisted in the DO; reconcile through a subsequent read/claim.
                prepared = await bridge.request("POST", "setup", headers={"X-A13N-Cloudflare-Token": token})
            if not isinstance(prepared, dict) or prepared.get("phase") != "ready":
                raise failure("cloudflare", "provider_bootstrap_pending", Category.UNAVAILABLE)
        finally:
            await bridge.close()
        return endpoint
    finally:
        await api.close()
