"""Browse OOMOL OpenConnector providers and Actions, then test a personal/runtime connection."""

from __future__ import annotations

import argparse
from uuid import uuid4

import httpx2
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.connectivity.connectors.providers.openconnector.configuration import OpenConnectorConfiguration
from a13n_service.connectivity.connectors.providers.openconnector.runtime import OpenConnectorRuntime, RuntimeConnection
from a13n_service.connectivity.domain import JsonObject
from a13n_service.endpoint_policy import EndpointPolicy
from common import required_input, run_cli, show
from pydantic import TypeAdapter

RESPONSE_MAX_BYTES = 4 * 1024 * 1024


async def choose_connection(args: argparse.Namespace, runtime: OpenConnectorRuntime) -> RuntimeConnection:
    print("\n[4] Inspect connections visible to this personal key or runtime token", flush=True)
    connections = await runtime.connections(args.connector)
    show([connection.model_dump(mode="json") for connection in connections])
    if args.command == "authorize" or (not args.connection_id and not connections):
        console = "https://console.oomol.com" if args.deployment == "cloud" else args.endpoint
        print(f"Open {console} and connect {args.connector} using its account/connection settings.")
        print("Personal/runtime credentials do not expose the project-key hosted OAuth API.")
        input("After completing authorization in the console, press Enter to refresh connections: ")
        connections = await runtime.connections(args.connector)
        show([connection.model_dump(mode="json") for connection in connections])
    if not connections:
        raise ValueError("No visible connection. Complete authorization in the selected deployment's console first.")
    connection_id = required_input(args.connection_id, "Exact connection ID from the list")
    selected = next((connection for connection in connections if connection.id == connection_id), None)
    if selected is None:
        raise ValueError("Selected connection is not visible to this credential for this provider")
    await runtime.verify_connection(selected)
    return selected


async def run(args: argparse.Namespace, key: str, client: httpx2.AsyncClient) -> int:
    configuration = OpenConnectorConfiguration(endpoint=args.endpoint, deployment=args.deployment)
    policy = EndpointPolicy.from_operator_allowlist(private_domains=args.allow_private_domain)
    runtime = OpenConnectorRuntime(
        ConnectorHttpClient(client, policy, response_max_bytes=RESPONSE_MAX_BYTES),
        configuration,
        ApiKeyCredentials(api_key=key),
    )
    print("\n[1] OOMOL OpenConnector runtime configuration", flush=True)
    show(configuration.model_dump(mode="json"))
    print("Credential: personal OOMOL API key or self-hosted runtime token (Authorization: Bearer).")
    print("\n[2] Browse provider directory before account authorization", flush=True)
    providers = await runtime.providers()
    print(f"Discovered {len(providers)} providers:")
    for provider in sorted(providers, key=lambda item: item.service):
        if not args.connector or provider.service == args.connector:
            print(f"  {provider.service}\t{provider.name}\t{', '.join(provider.authentication_methods)}")
    if args.command in {"test", "discover"}:
        if args.connector and not any(provider.service == args.connector for provider in providers):
            raise ValueError("Selected provider is absent from the directory")
        return 0
    args.connector = required_input(args.connector, "Choose a provider service from the directory")
    if not any(provider.service == args.connector for provider in providers):
        raise ValueError("Selected provider is absent from the directory")
    print("\n[3] Preview Action definitions before account authorization", flush=True)
    actions = await runtime.actions(args.connector)
    print(f"Discovered {len(actions)} Actions:")
    for action in actions:
        print(f"  {action.id}\t{action.description[:160]}")
    if args.command == "tools":
        return 0
    if args.command in {"inspect", "authorize"}:
        await choose_connection(args, runtime)
        return 0
    action_id = required_input(args.tool, "Exact Action ID to describe or try")
    action = next((action for action in actions if action.id == action_id), None)
    if action is None:
        raise ValueError("Action is absent from the selected provider catalog")
    show(action.model_dump(mode="json"))
    if args.command == "describe":
        return 0
    connection = await choose_connection(args, runtime)
    arguments = TypeAdapter(JsonObject).validate_json(
        args.arguments if args.arguments is not None else input("Action input as a JSON object [{}]: ") or "{}"
    )
    request_id = f"smoke-{uuid4().hex}"
    print("\n[5] Review the Action call (may change data in the connected app)", flush=True)
    show({"action": action.id, "connection": connection.model_dump(mode="json"), "input": arguments})
    if not args.execute and input("Type CALL to execute, or Enter to stop: ").strip() != "CALL":
        print("Stopped before Action execution.")
        return 0
    outcome = await runtime.execute_action(
        action=action, connection=connection, arguments=arguments, request_id=request_id
    )
    show(outcome.model_dump(mode="json"))
    if outcome.kind == "outcome_unknown":
        print("Outcome is unknown. Check the provider using request_id before retrying.")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Uses https://github.com/oomol-lab/open-connector and https://connector.oomol.com.
Set OPENCONNECTOR_API_KEY (or enter it at the hidden prompt). The key is a personal
OOMOL API key; self-hosted deployments use a runtime token. Project keys and /saas
end-user APIs are a separate contract and are not used here. No .env files are read.

Examples:
  bash scripts/provider-smoke/openconnector.sh
  bash scripts/provider-smoke/openconnector.sh discover
  bash scripts/provider-smoke/openconnector.sh tools --connector github
  bash scripts/provider-smoke/openconnector.sh describe --connector github --tool github.get_current_user
  bash scripts/provider-smoke/openconnector.sh authorize --connector github
  bash scripts/provider-smoke/openconnector.sh call --connector github --connection-id ID \\
    --tool github.get_current_user --arguments '{}' --execute

Authorize opens no API session: connect the account in the provider console after
previewing Actions, then return to inspect it. No account credentials are collected.
Calls recheck the exact connection ID/alias and Action definition before dispatch;
upstream aliases and schemas are mutable, so these checks are not atomic version pins.
No calls or authorization writes run automatically. Accounts are not auto-revoked.
For self-hosting use --deployment self_hosted --endpoint https://your-runtime.example.
Private hosts also require --allow-private-domain HOST. Foundation Service is not used.
""",
    )
    parser.set_defaults(provider="openconnector")
    parser.add_argument(
        "command",
        nargs="?",
        default="walkthrough",
        choices=("walkthrough", "test", "discover", "tools", "describe", "inspect", "authorize", "call"),
    )
    parser.add_argument("--connector", help="OOMOL provider service ID, such as github")
    parser.add_argument("--tool", help="Exact Action ID, such as github.get_current_user")
    parser.add_argument("--connection-id", help="Exact visible connection ID; the adapter verifies its alias")
    parser.add_argument("--arguments", help="Action input JSON object")
    parser.add_argument("--execute", action="store_true", help="Authorize the exact Action call without a CALL prompt")
    parser.add_argument("--endpoint", default=OpenConnectorConfiguration.model_fields["endpoint"].default)
    parser.add_argument("--deployment", choices=("cloud", "self_hosted"), default="cloud")
    parser.add_argument("--allow-private-domain", action="append", default=[], metavar="HOST")
    args = parser.parse_args()
    required = []
    if args.command not in {"walkthrough", "test", "discover"}:
        required.append("connector")
    if args.command in {"describe", "call"}:
        required.append("tool")
    if args.command == "call":
        required.extend(("connection_id", "arguments", "execute"))
    missing = ["--" + name.replace("_", "-") for name in required if not getattr(args, name)]
    if missing:
        parser.error("Required for this command: " + ", ".join(missing))
    return args


def main() -> int:
    return run_cli(parse_args(), run)


if __name__ == "__main__":
    raise SystemExit(main())
