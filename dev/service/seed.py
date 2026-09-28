"""The seeded local state: a fictional organization created through the public API.

Setup runs in order; the conversations then run in parallel, bounded by the Service's worker slots. Runs execute
for real against the scripted model (`dev/fixtures/model.py`) in `local` environments. `seed_verify.py` reads
the result back.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from dev.service.api import Api
from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD, Checkout
from dev.service.seed_agents import seed_agents
from dev.service.seed_assets import examples, store
from dev.service.seed_connections import seed_connections
from dev.service.seed_conversations import Talk, scenarios
from dev.service.seed_identity import MEMBERS, seed_identity
from dev.service.seed_lifecycle import revise_after_runs
from dev.service.seed_local import seed_local, stopped_environment
from dev.service.seed_memories import recorded, remembered, seed_memories
from dev.service.seed_providers import seed_providers
from dev.service.seed_resources import seed_skills, seed_subscription, seed_templates
from dev.service.seed_usage import seed_usage

# The Service's default `worker.slots`: more parallel conversations would only queue.
PARALLEL_CONVERSATIONS = 4


@dataclass(frozen=True, slots=True)
class Seeded:
    organization: str  # ID
    workspace: str  # ID
    index: dict[str, str]  # IDs of what the report names


def seed(api: Api, checkout: Checkout) -> Seeded:
    """Seed this checkout with public resources, real execution and fictional usage history."""
    model_url = checkout.model_url
    workspace = api.first_workspace()
    api.workspace_id = workspace["id"]
    org, ws = f"/api/v1/organizations/{workspace['organization_id']}", f"/api/v1/workspaces/{workspace['id']}"
    index = seed_identity(api, org, ws)
    providers = seed_providers(api)
    local = seed_local(api, model_url)
    skills = seed_skills(api)
    seed_templates(api, providers["environment"], local.template)
    subscription = seed_subscription(api, model_url)
    connections = seed_connections(api, model_url, providers["connector"]["composio"])
    search = providers["web"]["brave"]
    cast = seed_agents(api, local, connections["ready"], search, skills)
    memories = seed_memories(api, local.model, model_url)
    assets = {example.name: store(api, example) for example in examples()}
    talk = Talk(api)
    jobs = (
        *scenarios(talk, cast, local.environment, assets),
        partial(remembered, talk, memories),
        partial(recorded, talk, memories),
        partial(stopped_environment, api, local.template),
    )
    with ThreadPoolExecutor(PARALLEL_CONVERSATIONS) as pool:
        for found in pool.map(lambda job: job(), jobs):
            index |= found
    last_run = api.get(f"/api/v1/runs/{index['conversation_last_run']}")
    index |= revise_after_runs(talk, cast, skills["release-notes"], last_run)
    # A sub-agent's result continues its parent thread after the scenario returned.
    api.until("/api/v1/threads?limit=100", lambda page: all(item["current_run_id"] is None for item in page["items"]))
    deliveries = f"/api/v1/subscriptions/{subscription['id']}/deliveries"
    api.until(deliveries, lambda page: any(item["status"] == "delivered" for item in page["items"]))
    index |= seed_usage(api, checkout, local)
    return Seeded(workspace["organization_id"], workspace["id"], index)


def write_report(path: Path, console_url: str, seeded: Seeded, checks: Sequence[tuple[str, bool]]) -> None:
    lines = [
        "# Seeded local state",
        "",
        f"Sign in at {console_url} as `{ADMIN_EMAIL}` / `{ADMIN_PASSWORD}`. Members use the same password:",
        "",
        *(f"- {role}: `{email}` ({name})" for role, (email, name) in MEMBERS.items()),
        "",
        "## Seeded IDs",
        "",
        f"- organization: `{seeded.organization}`",
        f"- workspace: `{seeded.workspace}`",
        *(f"- {name}: `{value}`" for name, value in seeded.index.items()),
        "",
        "## Verification",
        "",
        *(f"- [{'x' if passed else ' '}] {name}" for name, passed in checks),
        "",
    ]
    path.write_text("\n".join(lines))
