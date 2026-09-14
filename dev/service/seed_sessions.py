"""Bounded parallel Session creation with one local workspace per execution slot."""

from time import perf_counter

import anyio
from a13n_service.settings import Settings

from .seed_client import Client
from .seed_environments import local_workspace
from .seed_journeys import run


async def bulk_sessions(
    client: Client, base: str, catalog: dict, settings: Settings, count: int
) -> tuple[list[dict], list[dict]]:
    if count < 1:
        raise ValueError("Seed Session count must be positive")
    concurrency = min(count, settings.worker.concurrency)
    started = perf_counter()
    workspaces = [
        await local_workspace(
            client,
            base,
            catalog["environment_provider_id"],
            settings.filesystem.root / "bulk" / f"slot-{slot + 1:02d}",
            f"Local bulk workspace {slot + 1:02d}",
        )
        for slot in range(concurrency)
    ]
    print(f"Creating {count} Sessions with {concurrency} isolated execution slots...", flush=True)
    results: dict[int, dict] = {}
    pending: dict[str, anyio.Event] = {}
    completed_runs: dict[str, dict] = {}
    agents, assets = catalog["agents"], catalog["assets"]

    async def wait_for_run(run_id: str, expected: str) -> dict:
        ready = pending[run_id] = anyio.Event()
        try:
            with anyio.fail_after(90):
                await ready.wait()
            result = completed_runs.pop(run_id)
            if result["status"] != expected:
                raise RuntimeError(f"Seed Run ended as {result['status']}; expected {expected}")
            return result
        finally:
            pending.pop(run_id, None)

    async def observe() -> None:
        while True:
            if pending:
                unseen = set(pending)
                async for page in client.pages(base + "/runs", params={"limit": min(200, concurrency)}):
                    for result in page["items"]:
                        unseen.discard(result["id"])
                        ready = pending.get(result["id"])
                        if (
                            ready is not None
                            and result["status"] in {"completed", "failed", "cancelled", "waiting"}
                            and result.get("sealed_at")
                        ):
                            completed_runs[result["id"]] = result
                            ready.set()
                    if not unseen:
                        break
            await anyio.sleep(0.1)

    async def fill_slot(slot: int) -> None:
        # Reuse a directory only after its previous Run is sealed. Distinct slots
        # never share Environment state, Skill materialization, or input files.
        for index in range(slot, count, concurrency):
            prompt = ("[fail] " if index % 20 == 19 else "[long] " if index % 12 == 0 else "") + (
                f"Review fictional release brief {index + 1}. 请检查导航、历史消息和附件的交互体验。"
            )
            results[index] = await run(
                client,
                base,
                agents[index % len(agents)],
                prompt,
                environment_id=workspaces[slot]["environment_id"],
                wait_for_run=wait_for_run,
                asset_id=[assets[(index + offset) % len(assets)] for offset in (0, 1, 3)] if index % 12 == 0 else None,
            )
            completed = len(results)
            if completed % 20 == 0 or completed == count:
                print(f"Created {completed}/{count} Sessions ({perf_counter() - started:.1f}s)", flush=True)

    async with anyio.create_task_group() as observation:
        observation.start_soon(observe)
        async with anyio.create_task_group() as group:
            for slot in range(concurrency):
                group.start_soon(fill_slot, slot)
        observation.cancel_scope.cancel()
    # Completion order must not change the Agent/attachment selected for the
    # retained long conversation and its revision-history checks.
    return [results[index] for index in range(count)], workspaces
