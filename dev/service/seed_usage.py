"""Deterministic fictional history for Usage, inserted only by the local seeded reset.

Public APIs create resources. Historical execution facts are inserted with their original
clocks and intact constraints; no production backdating API or trigger bypass is needed.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from a13n_harness import HarnessState
from a13n_harness.pricing import ModelCostInput, ModelPricingEntry
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageSnapshot
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.attempts import Lease
from a13n_service.runs.checkpoints import DisplayPointer, RunState, StatePointer, publish
from a13n_service.runs.display import Display, Item, StreamPosition
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, SessionRow, ThreadRow, UsageRecordRow
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage
from sqlalchemy import insert, select

from dev.service.api import Api, Json
from dev.service.checkout import Checkout
from dev.service.seed_conversations import Talk
from dev.service.seed_local import Local


@dataclass(frozen=True)
class Template:
    organization_id: str
    workspace_id: str
    principal_id: str
    agent_id: str
    agent_revision_id: str
    authority: dict


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    input_tokens: int
    output_tokens: int
    cached_percent: int
    requests: int
    duration: int
    input_price: str | None
    output_price: str | None


PROFILES = (
    Profile("usage-economy", "Support assistant", 8000, 600, 85, 2, 8, "1", "4"),
    Profile("usage-reasoning", "Research analyst", 24000, 4000, 25, 5, 95, "4", "20"),
    Profile("usage-unpriced", "Experimental assistant", 5000, 1100, 0, 1, 18, None, None),
)


def seed_usage(api: Api, checkout: Checkout, local: Local) -> dict[str, str]:
    """Create three readable model/agent pairs, then 30 days of inspectable history."""
    templates = []
    for profile in PROFILES:
        pricing = (
            None
            if profile.input_price is None
            else {
                "provider": "local",
                "model": profile.key,
                "source": "manual",
                "source_revision": "usage-fixture-v1",
                "rules": [
                    {
                        "rule_id": "fictional",
                        "prices": [
                            {"price_key": "input_mtok", "price": profile.input_price},
                            {"price_key": "cache_read_mtok", "price": str(Decimal(profile.input_price) / 10)},
                            {"price_key": "output_mtok", "price": profile.output_price},
                        ],
                    }
                ],
            }
        )
        model = api.post(
            "/api/v1/models",
            {
                "provider_id": local.model["provider_id"],
                "key": profile.key,
                "name": profile.key.removeprefix("usage-").title() + " (fictional)",
                "description": "Fictional usage fixture; calls the local scripted model.",
                "config": {"model_name": profile.key, "model_api": "openai.chat_completions"},
                "pricing": pricing,
            },
        )
        agent = api.post(
            "/api/v1/agents",
            {
                "name": profile.name,
                "description": "Fictional usage history for local review.",
                "config": {"model": model["key"]},
                "labels": {"fixture": "usage"},
            },
        )
        templates.append(Talk(api).start(agent, "Summarize this fictional usage example."))
    return asyncio.run(_history(checkout, templates))


async def _history(checkout: Checkout, source_runs: list[Json]) -> dict[str, str]:
    storage = Storage(checkout.database_url)
    objects = LocalObjects(checkout.objects, max_bytes=16 * 1024 * 1024, timeout=10)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    index = {"usage_history_start": (today - timedelta(days=30)).isoformat(), "usage_history_end": today.isoformat()}
    try:
        async with short_session(storage) as session:
            models = {
                row.key: (row.id, row.pricing)
                for row in await session.scalars(
                    select(ModelRow).where(
                        ModelRow.workspace_id == source_runs[0]["workspace_id"],
                        ModelRow.key.in_([p.key for p in PROFILES]),
                    )
                )
            }
            templates = []
            for source in source_runs:
                row = await session.get_one(RunRow, source["id"])
                templates.append(
                    Template(
                        row.organization_id,
                        row.workspace_id,
                        row.principal_id,
                        row.agent_id,
                        row.agent_revision_id,
                        row.authority,
                    )
                )
        for offset in range(30, 0, -1):
            if offset % 7 in (0, 6):  # Deliberately empty days.
                continue
            for number, (profile, template) in enumerate(zip(PROFILES, templates, strict=True)):
                # Recent growth, a visible spike, and distinct per-agent request counts.
                count = 1 + (30 - offset) // 10 + (4 if offset == 3 else 0)
                for ordinal in range(count):
                    started = today - timedelta(days=offset) + timedelta(hours=9 + number * 3, minutes=ordinal * 10)
                    run_id = await _run(storage, objects, template, profile, models[profile.key], started, ordinal)
                    index[f"usage_{profile.key}"] = run_id
        return index
    finally:
        await storage.close()


async def _run(
    storage: Storage,
    objects: LocalObjects,
    template: Template,
    profile: Profile,
    model: tuple[str, dict | None],
    started: datetime,
    ordinal: int,
) -> str:
    organization_id, workspace_id = template.organization_id, template.workspace_id
    session_id, thread_id, run_id, attempt_id, entry_id = (
        new_object_id(kind) for kind in ("sess", "thr", "run", "rat", "entry")
    )
    ended = started + timedelta(seconds=profile.duration + ordinal * 7)
    prompt = "Summarize this fictional usage example."
    answer = f"Fictional {profile.name.lower()} run for {started.date()}."
    snapshot = _snapshot(profile, model[1], thread_id, started, ended, ordinal)
    harness_run_id = snapshot.run_id
    records = snapshot.records
    state, display = _checkpoint(snapshot, profile.key, prompt, answer, started, ended)
    lease = Lease(run_id, attempt_id, thread_id, organization_id, workspace_id, 1, "seed", "seed")
    state_ref = await publish(objects, lease, "state", state.model_dump_json().encode())
    display_ref = await publish(objects, lease, "display", display.model_dump_json().encode())
    scope = {"organization_id": organization_id, "workspace_id": workspace_id}
    payload = {"content": [{"type": "text", "text": prompt}]}
    async with transaction(storage) as session:
        await session.execute(
            insert(SessionRow).values(
                **scope,
                id=session_id,
                labels={"fixture": "usage", "a13n.console": "debug"},
                created_by_id=template.principal_id,
                created_at=started,
                updated_at=ended,
            )
        )
        await session.execute(
            insert(ThreadRow).values(
                **scope,
                id=thread_id,
                session_id=session_id,
                origin="new",
                mcp_headers={},
                labels={"fixture": "usage"},
                created_at=started,
                updated_at=ended,
            )
        )
        await session.execute(
            insert(RunRow).values(
                **scope,
                id=run_id,
                session_id=session_id,
                thread_id=thread_id,
                agent_id=template.agent_id,
                agent_revision_id=template.agent_revision_id,
                revision_selection="pinned",
                principal_id=template.principal_id,
                authority=template.authority,
                options={},
                options_digest=hashlib.sha256(canonical_json({})).hexdigest(),
                mcp_headers={},
                environment_mounts=[],
                memory_mounts=[],
                source_entry_id=entry_id,
                trigger="input",
                lineage="root",
                status="completed",
                available_at=started,
                attempts=1,
                max_attempts=3,
                started_at=started,
                sealed_at=ended,
                created_at=started,
                updated_at=ended,
                checkpoint=StatePointer(
                    key=state_ref.key, digest=state_ref.digest, size=state_ref.size, format=1, seq=1, attempt=1
                ).model_dump(),
                display=DisplayPointer(
                    key=display_ref.key,
                    digest=display_ref.digest,
                    size=display_ref.size,
                    format=1,
                    position=display.position,
                ).model_dump(),
                output=answer,
                usage_at_seal={
                    "requests": len(records),
                    "input_tokens": snapshot.summary.input_tokens,
                    "output_tokens": snapshot.summary.output_tokens,
                },
                labels={"fixture": "usage"},
            )
        )
        await session.execute(
            insert(AttemptRow).values(
                **scope,
                id=attempt_id,
                run_id=run_id,
                number=1,
                status="succeeded",
                start_reason="initial",
                worker_id="seed",
                worker_build="fixture",
                harness_run_id=harness_run_id,
                lease_token_hash="0" * 64,
                lease_expires_at=ended,
                heartbeat_at=ended,
                created_at=started,
                started_at=started,
                finished_at=ended,
                updated_at=ended,
            )
        )
        await session.execute(
            insert(InboxEntryRow).values(
                **scope,
                id=entry_id,
                thread_id=thread_id,
                kind="message",
                delivery="next_run",
                position=1,
                principal_id=template.principal_id,
                authority=template.authority,
                payload=payload,
                size=len(canonical_json(payload)),
                agent_id=template.agent_id,
                agent_revision_id=template.agent_revision_id,
                options={},
                status="consumed",
                assigned_run_id=run_id,
                incorporated_checkpoint_seq=1,
                created_at=started,
                finished_at=ended,
            )
        )
        for record in records:
            assert isinstance(record, ModelUsageRecord)
            payload = record.model_dump(mode="json")
            await session.execute(
                insert(UsageRecordRow).values(
                    **scope,
                    id=record.record_id,
                    run_id=run_id,
                    run_attempt_id=attempt_id,
                    harness_run_id=harness_run_id,
                    call_id=record.call_id,
                    record=payload,
                    model_id=model[0],
                    price_snapshot=model[1],
                    digest=hashlib.sha256(
                        canonical_json([payload, run_id, attempt_id, model[0], model[1]])
                    ).hexdigest(),
                    ingested_at=record.response_timestamp,
                )
            )
        payload = snapshot.model_dump(mode="json")
        await session.execute(
            insert(UsageRecordRow).values(
                **scope,
                id=snapshot.usage_id,
                run_id=run_id,
                run_attempt_id=attempt_id,
                harness_run_id=harness_run_id,
                record=payload,
                digest=hashlib.sha256(canonical_json([payload, run_id, attempt_id, None, None])).hexdigest(),
                ingested_at=ended,
            )
        )
        thread = await session.get_one(ThreadRow, thread_id)
        thread.head_run_id = thread.last_run_id = run_id
        (await session.get_one(SessionRow, session_id)).last_run_id = run_id
    return run_id


def _snapshot(
    profile: Profile, price: dict | None, thread_id: str, started: datetime, ended: datetime, ordinal: int
) -> UsageSnapshot:
    """Use the Harness pricing contract for synthetic requests with known cache proportions."""
    harness_run_id = new_object_id("hrun")
    instance_id = new_object_id("agent")
    pricing = ModelPricingEntry.model_validate(price) if price else None
    records = []
    for request in range(profile.requests):
        timestamp = started + (ended - started) * ((request + 1) / profile.requests)
        native = RequestUsage(
            input_tokens=profile.input_tokens + ordinal * 1000,
            output_tokens=profile.output_tokens,
            cache_read_tokens=(profile.input_tokens + ordinal * 1000) * profile.cached_percent // 100,
        )
        quote = (
            pricing.quote(
                ModelCostInput(
                    model_name=profile.key,
                    provider_name="local",
                    provider_url=None,
                    request_started_at=started,
                    response_timestamp=timestamp,
                    usage=native,
                ),
                source="custom",
                revision="usage-fixture-v1",
            )
            if pricing
            else None
        )
        native.cost = quote.cost_usd if quote else None
        records.append(
            ModelUsageRecord(
                record_id=new_object_id("usage"),
                run_id=harness_run_id,
                agent_instance_id=instance_id,
                response_ordinal=request,
                response_state="complete",
                response_timestamp=timestamp,
                request_started_at=started,
                model_id=profile.key,
                model_name=profile.key,
                provider_name="local",
                call_id=new_object_id("call"),
                request_usage=BoundedRequestUsage.from_request_usage(native),
                cost_source="custom" if quote else "unknown",
                pricing_status="applied" if quote else "declined",
                pricing_revision=quote.pricing_revision if quote else None,
                pricing_rule_id=quote.rule_id if quote else None,
            )
        )
    return UsageSnapshot(
        usage_id=new_object_id("usage"),
        thread_id=thread_id,
        run_id=harness_run_id,
        agent_instance_id=instance_id,
        sequence=1,
        records=tuple(records),
    )


def _checkpoint(
    snapshot: UsageSnapshot, model: str, prompt: str, answer: str, started: datetime, ended: datetime
) -> tuple[RunState, Display]:
    """Keep historical Runs inspectable through their normal checkpoint and display APIs."""
    messages = [ModelRequest(parts=[UserPromptPart(content=prompt, timestamp=started)])]
    history = [
        *messages,
        *(
            ModelResponse(
                parts=[TextPart(content=answer)],
                model_name=model,
                timestamp=record.response_timestamp,
                usage=RequestUsage(**record.request_usage.model_dump(exclude={"audio_seconds"})),
            )
            for record in snapshot.records
            if isinstance(record, ModelUsageRecord)
        ),
    ]
    state = RunState(
        seq=1,
        attempt=1,
        harness=HarnessState.new(
            thread_id=snapshot.thread_id,
            message_history=history,
            agent_context_state=AgentContextStateSnapshot(
                entries={"a13n.usage": CapabilityState(version="1", data=snapshot.model_dump(mode="json"))}
            ),
        ),
    )
    display = Display(
        position=StreamPosition(attempt=1, sequence=2),
        items=[
            Item(
                id=new_object_id("msg"),
                kind="text_message",
                state="completed",
                first_stream_id="1-1",
                last_stream_id="1-1",
                started_at=started,
                ended_at=ended,
                content={"role": "assistant", "text": answer},
            ),
            Item(
                id=new_object_id("obs"),
                kind="observation",
                state="completed",
                first_stream_id="1-2",
                last_stream_id="1-2",
                started_at=ended,
                ended_at=ended,
                content={
                    "name": "a13n.harness.usage",
                    "value": {
                        "type": "usage_report",
                        "records": [record.model_dump(mode="json") for record in snapshot.records],
                    },
                },
            ),
        ],
    )
    return state, display
