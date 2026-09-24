"""Browse Composio toolkits and tools, authorize an account, and test a call without a13n Service."""

from __future__ import annotations

import argparse
import sys
from getpass import getpass
from time import monotonic
from uuid import uuid4

import httpx2
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.connector.composio.configuration import COMPOSIO_ENDPOINT
from a13n_harness.providers.connector.contracts import (
    AdapterConnectionStatus,
    ConnectionBinding,
    ConnectorConnectionRuntime,
    ConnectorProviderError,
    ConnectorProviderRuntime,
    ConnectorTool,
    JsonObject,
    SetupCompletionMethod,
    SetupContext,
    ToolCatalog,
)
from a13n_harness.providers.connector.directory import DirectoryBudget, directory_items
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.connector.validation import required_object, required_string
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from common import required_input, run_cli, show
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

RESPONSE_MAX_BYTES = 4 * 1024 * 1024


async def discover_tools(catalog: ToolCatalog) -> tuple[tuple[ConnectorTool, ...], str]:
    """Page one catalog under the shared ceilings; a Host adds its own validation and budgets."""
    tools: list[ConnectorTool] = []
    version: str | None = None
    cursor: str | None = None
    for _ in range(DISCOVERY_MAX_PAGES):
        page = await catalog.discover_tools(cursor=cursor)
        if version is None:
            version = page.provider_version
        elif page.provider_version != version:
            raise ConnectorProviderError("discovery_incompatible")
        tools.extend(page.items)
        if len(tools) > DISCOVERY_MAX_TOOLS:
            raise ConnectorProviderError("directory_too_large")
        cursor = page.next_cursor
        if cursor is None:
            return tuple(tools), version
    raise ConnectorProviderError("directory_too_large")


async def inspect_connection(connection: ConnectorConnectionRuntime, *, require_ready: bool) -> None:
    inspection = await connection.inspect()
    show(inspection.model_dump(mode="json"))
    if require_ready and inspection.status != AdapterConnectionStatus.ready:
        raise ValueError("Connection is not ready. Complete authorization in the provider dashboard first.")


async def authorize_account(args: argparse.Namespace, provider: ConnectorProviderRuntime) -> bool:
    print("\n[4] Discover hosted authorization options", flush=True)
    connector = await provider.discover_connector(args.connector)
    if not connector.authentication_methods:
        raise ValueError(
            "No supported hosted auth configuration. Create an OAuth auth configuration in the provider dashboard first."
        )
    show(connector.model_dump(mode="json"))
    setup: JsonObject = {"auth_config_id": required_input(args.auth_config_id, "Auth configuration ID from the schema")}
    properties = required_object(connector.setup_schema.get("properties"))
    setup["toolkit_version"] = required_string(required_object(properties.get("toolkit_version")), "const")
    callback = required_input(
        args.callback_url, "Your browser callback URL (the script does not host a callback server)"
    )
    EndpointPolicy(require_https=True).validate_syntax(callback)
    Draft202012Validator(connector.setup_schema).validate(setup)
    args.user_id = args.user_id or f"smoke-user-{uuid4().hex}"
    context = SetupContext(
        connector_key=args.connector,
        external_user_correlation=args.user_id,
        callback_url=callback,
    )
    show({"setup": setup, "user_id": args.user_id, "callback_url": callback})
    if input("Type AUTHORIZE to create a provider authorization session, or Enter to stop: ").strip() != "AUTHORIZE":
        print("Stopped before authorization.")
        return False
    started = await provider.start_setup(setup=setup, context=context)
    args.connection_id = started.external_ref
    show({"connection_id": args.connection_id, "user_id": args.user_id, "authorization_url": started.redirect_url})
    print("Keep the connection ID and user ID to inspect or reuse this account later. The script does not revoke it.")
    if started.redirect_url is None:
        raise ValueError("Provider returned no authorization URL; inspect the saved connection ID in its dashboard")
    if started.completion_method == SetupCompletionMethod.browser_confirmation:
        print("Only confirm credentials you entered yourself. Composio cannot verify which browser submitted them.")
    input("Open the authorization URL in your browser, complete authorization, then press Enter to verify: ")
    if started.completion_method == SetupCompletionMethod.oauth_verifier:
        session_uri = getpass("session_uri from the callback (hidden): ").strip()
        if not session_uri:
            raise ValueError("A session_uri is required to complete authorization")
        if started.external_ref is None:
            raise ValueError("Provider returned no connected account ID")
        inspection = await provider.complete_setup(
            session_uri=session_uri,
            context=context,
            expected_external_ref=started.external_ref,
        )
        show(inspection.model_dump(mode="json"))
    return True


async def use_connection(
    args: argparse.Namespace, provider: ConnectorProviderRuntime, selected: ConnectorTool | None
) -> int:
    if args.connection_id is None:
        if args.command != "authorize":
            args.connection_id = (
                input("\nExisting connection ID, or press Enter to authorize a new account: ").strip() or None
            )
        if args.connection_id is None and not await authorize_account(args, provider):
            return 0
    print("\n[5] Bind and verify the account and its owner", flush=True)
    binding = ConnectionBinding(
        connector_key=args.connector,
        external_ref=required_input(args.connection_id, "Provider connection ID"),
        external_user_correlation=required_input(args.user_id, "Provider user ID (must match the account owner)"),
    )
    connection = provider.connect(binding)
    await inspect_connection(connection, require_ready=args.command != "inspect")
    if args.command in {"inspect", "authorize"}:
        return 0
    if selected is None:
        raise ValueError("A tool must be selected before authorization")
    tools, _ = await discover_tools(connection)
    current = next((tool for tool in tools if tool.key == selected.key), None)
    if current != selected:
        raise ValueError(
            "Tool changed or disappeared after authorization. Preview the current definition before calling again."
        )
    raw_arguments = args.arguments
    if raw_arguments is None:
        raw_arguments = input("Tool arguments as a JSON object [{}]: ") or "{}"
    arguments = TypeAdapter(JsonObject).validate_json(raw_arguments)
    Draft202012Validator(selected.input_schema).validate(arguments)
    request_id = f"smoke-{uuid4().hex}"
    print("\n[6] Request to execute (may change data in the connected app)", flush=True)
    show(
        {
            "tool": selected.key,
            "version": selected.provider_version,
            "arguments": arguments,
            "request_id": request_id,
        }
    )
    if not args.execute:
        if input("Type CALL to execute this exact request, or press Enter to stop: ").strip() != "CALL":
            print("Stopped before tool execution.")
            return 0

    # Recheck ownership and readiness after the user has reviewed the request.
    await inspect_connection(connection, require_ready=True)
    started = monotonic()
    outcome = await connection.execute_tool(
        tool_key=selected.key,
        provider_version=selected.provider_version,
        arguments=arguments,
        request_id=request_id,
    )
    show({"elapsed_seconds": round(monotonic() - started, 2), **outcome.model_dump(mode="json")})
    if outcome.kind == "outcome_unknown":
        print("Execution outcome is unknown. Check the provider using request_id before retrying.", file=sys.stderr)
        return 1
    if selected.output_schema is not None:
        Draft202012Validator(selected.output_schema).validate(outcome.result)
    return 0


async def browse_directory(key: str, http: ConnectorHttpClient) -> set[str]:
    print("\n[0] Browse the upstream connector directory before selecting a connector", flush=True)
    print(f"Endpoint: {COMPOSIO_ENDPOINT}", flush=True)
    items = await directory_items(
        http,
        endpoint=COMPOSIO_ENDPOINT,
        api_key=key,
        path="/api/v3.1/toolkits",
        budget=DirectoryBudget(),
    )
    directory: dict[str, str] = {}
    for item in items:
        slug = required_string(item, "slug", max_length=128)
        if slug in directory:
            raise ValueError("Duplicate connector in upstream directory")
        directory[slug] = required_string(item, "name", max_length=128)
    print(f"Discovered {len(directory)} connectors across all returned pages:", flush=True)
    for slug, name in sorted(directory.items()):
        print(f"  {slug}\t{name}")
    print(
        "Directory entries are candidates; account authorization and runtime compatibility are checked after selection."
    )
    return set(directory)


async def run(args: argparse.Namespace, key: str, client: httpx2.AsyncClient) -> int:
    policy = EndpointPolicy()
    definition = ProviderCatalog(BUILT_IN_CONNECTOR_PROVIDERS).require(args.provider)
    http = ConnectorHttpClient(client, policy, response_max_bytes=RESPONSE_MAX_BYTES)
    if args.connector is None and args.command in {"walkthrough", "discover"}:
        available = await browse_directory(key, http)
        if args.command == "discover":
            return 0
        if not available:
            raise ValueError("The upstream connector directory is empty")
        args.connector = required_input(None, "Choose a connector slug from the directory")
        if args.connector not in available:
            raise ValueError("Selected connector is absent from the directory")
    args.connector = required_input(args.connector, "Connector/toolkit slug (for example github)")
    configuration: JsonObject = {}
    async with definition.open(configuration, {"api_key": key}, http=http) as provider:
        print("\n[1] Validate provider configuration", flush=True)
        show({"provider": args.provider, "configuration": configuration})
        if args.command == "test":
            await provider.test()
            print("Provider connection test passed.", flush=True)
            return 0
        if args.command == "discover":
            print("\nDiscover selected connector and available authentication configurations", flush=True)
            connector = await provider.discover_connector(args.connector)
            show(connector.model_dump(mode="json"))
            return 0
        if args.command == "inspect":
            return await use_connection(args, provider, None)
        print("\n[2] Preview tool definitions before account authorization", flush=True)
        tools, version = await discover_tools(provider.tool_catalog(args.connector))
        print(f"Discovered {len(tools)} tools; catalog version: {version}", flush=True)
        print("This is a provider catalog preview. It grants no permission to execute a tool.")
        for tool in tools:
            print(f"  {tool.key}\t{tool.provider_version}\t{tool.description[:160]}")
        if args.command == "tools":
            return 0
        if args.command == "authorize":
            return await use_connection(args, provider, None)
        tool_key = required_input(args.tool, "Exact tool key to describe or try")
        selected = next((tool for tool in tools if tool.key == tool_key), None)
        if selected is None:
            raise ValueError("Tool is absent from the current catalog")
        print("\n[3] Selected tool definition", flush=True)
        show(selected.model_dump(mode="json"))
        if args.command == "describe":
            return 0
        return await use_connection(args, provider, selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples (replace the placeholder IDs with values from your provider dashboard):
  bash scripts/provider-smoke/composio.sh
  bash scripts/provider-smoke/composio.sh discover
  bash scripts/provider-smoke/composio.sh discover --connector github
  bash scripts/provider-smoke/composio.sh tools --connector github
  bash scripts/provider-smoke/composio.sh describe --connector github --tool TOOL
  bash scripts/provider-smoke/composio.sh authorize --connector github
  bash scripts/provider-smoke/composio.sh call --connector github --connection-id ACCOUNT --user-id USER \\
    --tool TOOL --arguments '{}' --execute

Set COMPOSIO_API_KEY, or enter the key at the hidden prompt.
The walkthrough lists the upstream connector directory before asking you to choose.
Discover without --connector lists all directory entries using bounded pagination;
discovery fails on exceeded budgets rather than silently truncating the catalog.
With --connector, discover shows that connector's authentication configuration schema.
Tools and describe preview definitions without an authorized account or a user ID.
The walkthrough then accepts an existing connection ID or starts hosted authorization.
Authorize previews tools and asks for AUTHORIZE before creating a provider session.
An OAuth auth configuration must exist in the provider dashboard. Composio also needs
a browser callback URL supplied by you; this script does not host a callback server.
Complete authorization in your browser, then return to verify the account. Account IDs
are printed for reuse; nothing is saved locally, and accounts are never auto-revoked.
Call requires an existing authorized account and its exact provider user ID; it rechecks
ownership, readiness, and the current tool definition before dispatch.
Calls are never automatically retried. --execute authorizes the selected tool call.
The scripts do not load .env files or use a13n Service authentication/storage.
""",
    )
    parser.set_defaults(provider="composio")
    parser.add_argument(
        "command",
        nargs="?",
        default="walkthrough",
        choices=("walkthrough", "test", "discover", "inspect", "tools", "describe", "authorize", "call"),
    )
    parser.add_argument("--connector", help="Selected toolkit/provider slug; omit for full directory discovery")
    parser.add_argument("--connection-id", help="Existing connected account ID in the provider")
    parser.add_argument("--user-id", help="Exact provider user ID attached to that connected account")
    parser.add_argument("--tool", help="Exact tool key from discovery")
    parser.add_argument("--arguments", help="Tool arguments as a JSON object")
    parser.add_argument("--auth-config-id", help="Existing provider OAuth authentication configuration ID")
    parser.add_argument("--callback-url", help="Browser callback URL for Composio hosted authorization")
    parser.add_argument("--execute", action="store_true", help="Execute without the interactive CALL prompt")
    args = parser.parse_args()
    if args.command != "walkthrough":
        required = [] if args.command == "discover" else ["connector"]
        if args.command in {"inspect", "call"}:
            required += ["connection_id", "user_id"]
        if args.command in {"describe", "call"}:
            required += ["tool"]
        if args.command == "call":
            required += ["arguments", "execute"]
        missing = ["--" + name.replace("_", "-") for name in required if not getattr(args, name)]
        if missing:
            parser.error("Required for this command: " + ", ".join(missing))
    if args.command == "authorize" and args.connection_id:
        parser.error("authorize creates a new session; use inspect for an existing connection")
    return args


def main() -> int:
    return run_cli(parse_args(), run)


if __name__ == "__main__":
    raise SystemExit(main())
