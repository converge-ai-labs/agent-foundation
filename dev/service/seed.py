"""Prepare and verify the complete local development baseline."""

import json
from time import perf_counter

import httpx2
from a13n_service.app import create_app
from a13n_service.settings import Settings

from .model import MODEL_PORT, model_process
from .seed_client import Client
from .seed_connectivity import connectivity
from .seed_environments import environments
from .seed_execution import execution
from .seed_identity import PASSWORD, members, profiles
from .seed_journeys import journeys, run
from .seed_lifecycle import resource_history
from .seed_resources import resources
from .seed_sessions import bulk_sessions
from .seed_verify import report, verify


async def seed(settings: Settings, *, session_count: int = 3, model_port: int = MODEL_PORT) -> dict:
    # The temporary application owns the same production runtime and stops before
    # reset returns. No browser, listening Service port, or test authenticator.
    total_started = perf_counter()
    with model_process(model_port) as model_url:
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            phase_started = perf_counter()
            identity = app.state.runtime.control.identity
            issued = await identity.invitations.initialize(reissue=True)
            if issued is None:
                raise RuntimeError("Seeding requires a freshly migrated empty installation")
            origin = settings.iam.public_origin
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app),
                base_url=origin.replace("http://", "https://", 1),
                headers={"Origin": origin},
                timeout=60,
                trust_env=False,
            ) as http:
                client = Client(http)
                login = await client.request(
                    "POST",
                    f"/api/v1/invitations/{issued.invitation.id}/accept",
                    json={"token": issued.token, "password": PASSWORD},
                )
                http.headers["X-A13N-CSRF-Token"] = login["csrf_token"]
                organization = (await client.collection("/api/v1/organizations"))[0]
                workspace = (await client.collection(f"/api/v1/organizations/{organization['id']}/workspaces"))[0]
                empty = await client.request(
                    "POST",
                    f"/api/v1/organizations/{organization['id']}/workspaces",
                    expected=201,
                    json={"name": "Empty workspace / 空白体验"},
                )
                base = f"/api/v1/workspaces/{workspace['id']}"
                http.headers["X-A13N-Workspace-ID"] = workspace["id"]
                await profiles(client, organization["id"], workspace["id"])
                identity_scenarios = await members(client, base, app, origin)
                print(f"Seed identity and workspaces: {perf_counter() - phase_started:.2f}s", flush=True)
                phase_started = perf_counter()
                catalog = await resources(client, base, model_url, settings)
                assets, skills, agents = catalog["assets"], catalog["skills"], catalog["agents"]
                print(f"Seed catalog resources: {perf_counter() - phase_started:.2f}s", flush=True)
                print(
                    f"Prepared {len(agents)} Agents, {len(skills)} Skills and {len(assets)} Assets; creating Sessions...",
                    flush=True,
                )
                phase_started = perf_counter()
                runs, bulk_environments = await bulk_sessions(client, base, catalog, settings, session_count)
                print(f"Seed representative Sessions: {perf_counter() - phase_started:.2f}s", flush=True)
                # One genuine continued conversation for scroll and history review.
                phase_started = perf_counter()
                previous = next(run for run in runs if run["status"] == "completed")
                for index in range(12):
                    previous = await run(
                        client,
                        base,
                        previous["agent_id"],
                        f"[long] Follow-up {index + 1}: expand the review.",
                        previous=previous,
                    )
                print(f"Seed long conversation: {perf_counter() - phase_started:.2f}s", flush=True)
                print("Creating branching, retry, waiting, feedback, interruption and queue scenarios...", flush=True)
                phase_started = perf_counter()
                conversation_scenarios = await journeys(client, base, catalog, previous)
                conversation_scenarios.update(await execution(client, base, catalog))
                connectivity_scenarios = await connectivity(client, base, catalog, identity_scenarios, model_url)
                catalog["scenarios"].update(await environments(client, base, catalog, settings))
                catalog["scenarios"].update(await resource_history(client, base, catalog))
                print(f"Seed dedicated journeys: {perf_counter() - phase_started:.2f}s", flush=True)
                sessions = await client.collection(base + "/sessions")
                manifest = {
                    "workspace_id": workspace["id"],
                    "empty_workspace_id": empty["id"],
                    "agent_ids": agents,
                    "asset_ids": assets,
                    "skill_ids": skills,
                    "session_count": len(sessions),
                    "bulk_session_count": session_count,
                    "bulk_environments": bulk_environments,
                    "long_thread_id": previous["thread_id"],
                    "model_url": model_url,
                    "asset_checks": catalog["asset_checks"],
                    "scenarios": {
                        "identity": identity_scenarios,
                        "resources": catalog["scenarios"],
                        "conversations": conversation_scenarios,
                        "connectivity": connectivity_scenarios,
                    },
                }
                print("Verifying retained resources, pagination, relationships and outcomes...", flush=True)
                phase_started = perf_counter()
                manifest["coverage"] = await verify(client, manifest)
                await client.request("POST", "/api/v1/auth/logout", expected=204)
                print(f"Seed semantic verification: {perf_counter() - phase_started:.2f}s", flush=True)
    settings.filesystem.root.parent.joinpath("seed.json").write_text(json.dumps(manifest, indent=2) + "\n")
    settings.filesystem.root.parent.joinpath("seed-report.md").write_text(report(manifest))
    print(f"Public local account: {settings.iam.initial_admin_email} / {PASSWORD}", flush=True)
    print(f"Seed total: {perf_counter() - total_started:.2f}s", flush=True)
    return manifest
