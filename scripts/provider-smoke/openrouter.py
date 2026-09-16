"""Exercise the Model module against OpenRouter without a service or database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from getpass import getpass
from time import monotonic
from typing import cast

import httpx2
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import RuntimeProvider
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.settings import validate_settings
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings


@dataclass
class LocalProviderResolver:
    """Supply only this process's explicit credential to the connection probe."""

    provider: RuntimeProvider

    async def resolve_provider(
        self, *, provider_id: str, organization_id: str, workspace_id: str | None
    ) -> RuntimeProvider:
        return self.provider


def show(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def pause(message: str) -> None:
    input(f"\n{message} Press Enter to continue or Ctrl-C to exit.")


async def run(args: argparse.Namespace, key: str, client: httpx2.AsyncClient) -> None:
    registry = built_in_provider_registry()
    definition = registry.definition("openrouter")
    configuration = registry.validate_provider("openrouter", {}, credential_configured=bool(key))
    provider = RuntimeProvider("openrouter", configuration.configuration, configuration.endpoint, key)
    model_api = definition.default_model_api
    print("\n[1] Local Provider configuration validated (credential hidden)", flush=True)
    show({"type": provider.type, "endpoint": provider.endpoint, "model_api": model_api})

    operations = NativeProviderOperations(
        provider_resolver=LocalProviderResolver(provider), registry=registry, http_client=client
    )
    walkthrough = args.command == "walkthrough"
    if walkthrough:
        pause("Next: probe the OpenRouter connection through the Model module.")
    if args.command in {"walkthrough", "test"}:
        print("\n[2] Test Provider connection", flush=True)
        async with asyncio.timeout(60):
            await operations.test(provider_id="local", organization_id="local", workspace_id="local")
        print("Connection probe succeeded; this does not prove inference authorization.", flush=True)
        if args.command == "test":
            return

    model_id = args.model or input("\nEnter the full OpenRouter model ID to test (vendor/model): ").strip()
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef", model_key="local-smoke", upstream_model=model_id, model_api=model_api
    )
    if not model_id.strip():
        raise ValueError("Model ID must not be empty")
    if args.command != "call":
        print("\n[3] Local Model API settings (not upstream capability discovery)", flush=True)
        show({"upstream_model": model_id, "model_api": model_api})
        properties = cast(dict[str, object], definition.settings_schemas[model_api].get("properties", {}))
        print("Available settings:", ", ".join(sorted(properties)))
        if args.command == "describe":
            return

    settings = validate_settings(model_api, json.loads(args.settings))
    model = await NativeModelFactory(client, registry, EndpointPolicy()).build(snapshot, provider)
    print("\n[4] Settings validated and native Model constructed", flush=True)
    show({"upstream_model": model_id, "model_api": model_api, "settings": settings, "prompt": args.prompt})
    print(f"Native Model type: {type(model).__name__}", flush=True)
    if walkthrough:
        pause("Next: send a real inference request. This consumes OpenRouter quota.")
    print("\n[5] Invoke: POST /api/v1/chat/completions (consumes quota)", flush=True)
    started = monotonic()
    async with asyncio.timeout(120), model:
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(args.prompt)])],
            cast(ModelSettings, settings),
            ModelRequestParameters(),
        )
    show(
        {
            "elapsed_seconds": round(monotonic() - started, 2),
            "model": response.model_name,
            "finish_reason": response.finish_reason,
            "text": response.text,
            "usage": asdict(response.usage),
        }
    )
    if not response.text:
        print(
            "Request succeeded without text. Check finish_reason; reasoning models may need a larger max_tokens value."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", default="walkthrough", choices=("walkthrough", "test", "describe", "call")
    )
    parser.add_argument("--model", help="Full OpenRouter upstream model ID; required for describe/call")
    parser.add_argument(
        "--settings", default='{"max_tokens": 256}', help="JSON object containing native Model settings"
    )
    parser.add_argument("--prompt", default="Reply with a short greeting in English.")
    args = parser.parse_args()
    if args.command in {"describe", "call"} and not args.model:
        parser.error("describe/call requires --model")
    key = ""
    try:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            if not sys.stdin.isatty():
                parser.error(
                    "Set OPENROUTER_API_KEY or run in an interactive terminal to enter the key at a hidden prompt"
                )
            key = getpass("OpenRouter API key (hidden input): ").strip()
        if not key:
            raise ValueError("OpenRouter API key must not be empty")

        async def execute() -> None:
            async with httpx2.AsyncClient(timeout=60, follow_redirects=False) as client:
                await run(args, key, client)

        asyncio.run(execute())
    except (KeyboardInterrupt, EOFError):
        print("\nExited.", file=sys.stderr)
        return 130
    except Exception as error:
        message = str(error).replace(key, "[REDACTED]") if key else str(error)
        print(f"\nFailed: {type(error).__name__}: {message}", file=sys.stderr)
        print(
            "401/403: check key and permissions; 402: check credit; 429: rate limited; timeout: check network or retry later.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
