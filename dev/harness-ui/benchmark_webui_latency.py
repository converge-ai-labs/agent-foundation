"""Measure end-to-end Harness WebUI multi-stage latencies on a populated store.

Run: uv run --locked python dev/harness-ui/benchmark_webui_latency.py --active 1 4 --rounds 3
Outputs aggregate stage timings only; no prompt or output content leaves the temporary store.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import socket
import statistics
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from a13n_harness import HarnessState
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageSnapshot
from a13n_harness_ui.app import HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.display_history import import_display_history
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.shared_drafts import DraftCommand, composer_document
from a13n_harness_ui.storage import Continuation
from a13n_harness_ui.storage.continuations import publish_continuation
from a13n_harness_ui.storage.display import DisplayArchive, seal_display
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.thread_projection import build_thread_inspection
from a13n_harness_ui.webui import create_webui
from pycrdt import Doc, Text
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel
from websockets.asyncio.client import ClientConnection, connect


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return round(ordered[index], 2)


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "count": len(values),
        "p50_ms": round(statistics.median(values), 2),
        "p95_ms": percentile(values, 0.95),
        "max_ms": round(max(values), 2),
    }


def write_config(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "sample.py").write_text(("def synthetic_helper(x: int) -> int:\n    return x + 1\n") * 250)
    cfg = root / "a13n-harness-ui.yaml"
    cfg.write_text(
        'schema_version: "1"\n'
        "defaults:\n  project: project-main\n  agent: agent-assistant\n"
        "browser:\n  mode: disabled\n"
    )
    (root / "models").mkdir(exist_ok=True)
    (root / "models/primary.yaml").write_text(
        'schema_version: "1"\nkind: model\nid: model-primary\nname: Primary\n'
        "route: openai:gpt-5\nauthentication: {kind: api_key, env: OPENAI_API_KEY}\n"
    )
    (root / "agents").mkdir(exist_ok=True)
    (root / "agents/assistant.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-assistant\nname: Assistant\n'
        "model: model-primary\ninstructions: Synthetic benchmark assistant.\n"
    )
    (root / "projects").mkdir(exist_ok=True)
    (root / "projects/main.yaml").write_text(
        f'schema_version: "1"\nkind: project\nid: project-main\nname: Main\nroots:\n  - path: {workspace.as_posix()}\n'
    )
    return cfg


def build_populated_messages(turns: int, steps_per_turn: int, payload_bytes: int) -> list[ModelMessage]:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    blob = "x" * payload_bytes
    edit_before = "line_before\n" * max(1, payload_bytes // 48)
    edit_after = "line_after\n" * max(1, payload_bytes // 48)
    messages: list[ModelMessage] = []
    for turn in range(turns):
        ts = now + timedelta(minutes=turn * 3)
        messages.append(ModelRequest(parts=[UserPromptPart(f"Turn {turn} request " + "q" * 256)], timestamp=ts))
        for step in range(steps_per_turn):
            call_id = f"call_{turn}_{step}"
            messages.append(
                ModelResponse(
                    parts=[
                        ThinkingPart(f"Reasoning {turn}:{step} " + "r" * 512),
                        ToolCallPart(
                            tool_name="edit",
                            args={"file_path": f"/repo/src/mod_{step}.py", "old_string": "a", "new_string": "b"},
                            tool_call_id=call_id,
                        ),
                    ],
                    model_name="gpt-5",
                    timestamp=ts + timedelta(seconds=step * 2 + 1),
                )
            )
            messages.append(
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="edit",
                            content=f"Edited mod_{step}.py\n" + blob,
                            tool_call_id=call_id,
                            metadata={
                                "a13n.harness-ui.applied_edit": {
                                    "file_path": f"/repo/src/mod_{step}.py",
                                    "before": edit_before,
                                    "after": edit_after,
                                    "omitted": False,
                                }
                            },
                            timestamp=ts + timedelta(seconds=step * 2 + 2),
                        )
                    ],
                    timestamp=ts + timedelta(seconds=step * 2 + 2),
                )
            )
        messages.append(
            ModelResponse(
                parts=[TextPart(f"Completed turn {turn}.\n" + "a" * 1024)],
                model_name="gpt-5",
                timestamp=ts + timedelta(seconds=steps_per_turn * 2 + 3),
            )
        )
    return messages


async def populate_thread(
    app: HarnessUiApp,
    thread_id: str,
    *,
    turns: int,
    steps_per_turn: int,
    payload_bytes: int,
    usage_scopes: int,
    records_per_scope: int,
    draft_edits: int,
) -> dict[str, int]:
    messages = build_populated_messages(turns, steps_per_turn, payload_bytes)
    native = HarnessState.new(thread_id=thread_id, message_history=messages)
    display_full = import_display_history(messages)
    archive = await seal_display(app._store.objects, DisplayArchive(tail=display_full))
    thread_record = await app._store.threads.get(thread_id)
    assert thread_record is not None
    executor = app._root_runs._executor
    captured = await executor.capture(thread_id=thread_id, prompt="seed")
    cont = Continuation(
        harness_release="benchmark",
        run_composition=captured.published.reference,
        harness_state=native,
        display=archive,
        created_at=datetime.now(UTC),
    )
    cont_ref = await publish_continuation(app._store.objects, cont)
    projection = project_continuation(cont)
    selected = await app._store.threads.select_continuation(
        thread_id=thread_id,
        expected=None,
        replacement=cont_ref,
        excerpt=cont.excerpt,
        read_model=projection,
    )
    await app._store.publish_work(thread_id, cont_ref, native)
    inspection = build_thread_inspection(selected, cont)
    await app._store.inspections.publish(
        thread_id,
        cont_ref.logical_digest,
        inspection,
        expected_source=f"initial:{thread_record.initial_state.logical_digest}",
    )

    base_ts = datetime(2026, 6, 1, tzinfo=UTC)
    for scope_idx in range(usage_scopes):
        run_id = f"run-hist-{thread_id[-6:]}-{scope_idx}"
        agent_id = f"agent-{run_id}"
        child = bool(scope_idx % 3)
        records = tuple(
            ModelUsageRecord(
                call_id=f"call-{scope_idx}-{rec_idx}",
                record_id=f"rec-{scope_idx}-{rec_idx}",
                run_id=run_id,
                response_ordinal=rec_idx,
                agent_instance_id=agent_id,
                parent_agent_instance_id="agent-root" if child else None,
                response_state="complete",
                model_name="gpt-5",
                provider_name="openai",
                source="agent",
                response_timestamp=base_ts + timedelta(minutes=scope_idx, seconds=rec_idx),
                request_usage=BoundedRequestUsage(
                    input_tokens=80_000 + rec_idx * 500,
                    output_tokens=1_200,
                    cache_read_tokens=60_000,
                    cost=Decimal("0.045"),
                ),
                cost_source="catalog",
                pricing_status="applied",
            )
            for rec_idx in range(records_per_scope)
        )
        snapshot = UsageSnapshot(
            usage_id=f"usage-{thread_id[-6:]}-{scope_idx}",
            thread_id=thread_id,
            run_id=run_id,
            agent_instance_id=agent_id,
            parent_agent_instance_id="agent-root" if child else None,
            sequence=records_per_scope,
            records=records,
        )
        await app._store.usage.save(thread_id=thread_id, snapshot=snapshot)

    server_doc = composer_document()
    for idx in range(draft_edits):
        replica = composer_document()
        replica.apply_update(server_doc.get_update())
        sample = f"Draft iteration {idx} " + ("d" * 300)
        replica.get("text", type=Text).insert(0, sample)
        server_doc.apply_update(replica.get_update())
        replica2 = composer_document()
        replica2.apply_update(server_doc.get_update())
        del replica2.get("text", type=Text)[: len(sample)]
        server_doc.apply_update(replica2.get_update())
    doc = server_doc
    async with app.shared_draft_editor(thread_id) as (room, participant):
        await app.edit_shared_draft(
            thread_id,
            participant,
            DraftCommand(
                kind="sync",
                draft_id=room.draft_id,
                update_base64=base64.b64encode(doc.get_update()).decode(),
            ),
        )
    return {
        "native_state_bytes": len(native.message_history_json),
        "display_items": len(display_full.items),
        "tail_items": len(archive.tail.items),
        "usage_records": usage_scopes * records_per_scope,
        "draft_state_bytes": len(doc.get_update()),
    }


@asynccontextmanager
async def run_server(root: Path, cfg: Path) -> AsyncGenerator[tuple[str, str, HarnessUiApp]]:
    settings = HarnessUiSettings(
        storage=StorageSettings(data_root=root / "data"),
        pricing_auto_update=False,
        shutdown_timeout_seconds=2.0,
    )
    app: HarnessUiApp | None = None

    sample_file = (root / "workspace/sample.py").as_posix()

    async def scripted_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        del info
        await asyncio.sleep(0.01)
        last = messages[-1] if messages else None
        tool_ids = {
            part.tool_call_id
            for part in (last.parts if isinstance(last, ModelRequest) else ())
            if isinstance(part, ToolReturnPart)
        }
        tag = uuid4().hex[:8]
        if not any(cid.startswith("bench_r1_") for cid in tool_ids) and not any(
            cid.startswith("bench_r2_") for cid in tool_ids
        ):
            yield {0: DeltaThinkingPart(content="Inspecting first module.\n" + "t" * 400)}
            yield {
                1: DeltaToolCall(
                    name="view", tool_call_id=f"bench_r1_{tag}", json_args=json.dumps({"path": sample_file})
                )
            }
            return
        if any(cid.startswith("bench_r1_") for cid in tool_ids):
            yield {0: DeltaThinkingPart(content="Inspecting second pass.\n" + "t" * 400)}
            yield {
                1: DeltaToolCall(
                    name="view", tool_call_id=f"bench_r2_{tag}", json_args=json.dumps({"path": sample_file})
                )
            }
            return
        for chunk in ("Step one progress. " + "p" * 256, "Final synthetic answer chunk."):
            await asyncio.sleep(0.005)
            yield chunk

    from a13n_harness_ui import model_catalog

    orig_call = HarnessUiModelResolver.__call__
    orig_dir = model_catalog.fetch_directory

    async def bundled_dir():
        return model_catalog.bundled_models()

    async def resolve(self, context, model_id):
        del context, model_id
        return FunctionModel(stream_function=scripted_stream)

    HarnessUiModelResolver.__call__ = resolve  # type: ignore[method-assign]
    model_catalog.fetch_directory = bundled_dir  # type: ignore[assignment]
    try:

        @asynccontextmanager
        async def factory() -> AsyncGenerator[HarnessUiApp]:
            nonlocal app
            async with open_harness_ui_app(
                settings,
                configuration_path=cfg,
                host_mode="webui",
                share_computer=False,
                instrumentation=None,
            ) as opened:
                app = opened
                yield opened

        server = create_webui(factory, api_key="bench-key")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        native = uvicorn.Server(uvicorn.Config(server, log_level="error", lifespan="on", ws="websockets-sansio"))
        task = asyncio.create_task(native.serve(sockets=[sock]))
        try:
            while not native.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert app is not None
            yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}", app
        finally:
            native.should_exit = True
            await task
            sock.close()
    finally:
        HarnessUiModelResolver.__call__ = orig_call  # type: ignore[method-assign]
        model_catalog.fetch_directory = orig_dir  # type: ignore[assignment]


async def recv_until(ws: ClientConnection, pred) -> dict[str, Any]:
    while True:
        frame = json.loads(await ws.recv())
        if frame.get("kind") == "ping":
            await ws.send('{"version":1,"kind":"pong"}')
            continue
        if pred(frame):
            return frame


async def measure_focus_subscribe(
    ws_url: str, origin: str, thread_id: str, after: str | None = None
) -> dict[str, float]:
    async with connect(ws_url + "/api/realtime/connect", proxy=None, origin=origin, max_size=16 * 1024 * 1024) as ws:
        await ws.send('{"api_key":"bench-key"}')
        await ws.send(json.dumps({"version": 1, "kind": "subscribe", "channel": "sum", "stream": "summary"}))
        await recv_until(ws, lambda f: f.get("channel") == "sum")
        t0 = perf_counter()
        t_first: float | None = None
        cursor = after
        attempt = 0
        while True:
            ch = f"f_{attempt}"
            await ws.send(
                json.dumps(
                    {
                        "version": 1,
                        "kind": "subscribe",
                        "channel": ch,
                        "stream": "focus",
                        "root_thread_id": thread_id,
                        "after": cursor,
                    }
                )
            )
            first = await recv_until(ws, lambda f, c=ch: f.get("channel") == c)
            if t_first is None:
                t_first = perf_counter()
            inner = first["frame"]
            if inner["kind"] == "reset":
                reason = inner["reason"]
                had_cursor = cursor is not None
                cursor = None
                immediate = attempt == 0 and (
                    reason == "live_snapshot_changed"
                    or (had_cursor and reason in ("live_cursor_expired", "live_epoch_changed", "stream_cursor_invalid"))
                )
                attempt += 1
                if not immediate:
                    await asyncio.sleep(1.0)
                continue
            if inner["kind"] == "snapshot" and inner["snapshot"].get("root_stream") is not None:
                nxt = await recv_until(
                    ws, lambda f, c=ch: f.get("channel") == c and f["frame"]["kind"] in ("ready", "reset")
                )
                if nxt["frame"]["kind"] == "reset":
                    cursor = None
                    attempt += 1
                    continue
            t_ready = perf_counter()
            return {
                "first_frame_ms": (t_first - t0) * 1000,
                "usable_output_ms": (t_ready - t0) * 1000,
            }


async def exercise_turn(
    http_url: str,
    ws_url: str,
    thread_id: str,
    expired_cursor: str,
    delay_seconds: float = 0.0,
) -> dict[str, float]:
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    headers = {"Authorization": "Bearer bench-key"}
    async with (
        httpx.AsyncClient(base_url=http_url, headers=headers, trust_env=False, timeout=30.0) as api,
        connect(ws_url + "/api/realtime/connect", proxy=None, origin=http_url, max_size=16 * 1024 * 1024) as rt,
        connect(
            ws_url + f"/api/threads/{thread_id}/draft/connect", proxy=None, origin=http_url, max_size=16 * 1024 * 1024
        ) as draft_ws,
    ):
        await rt.send('{"api_key":"bench-key"}')
        await rt.send(json.dumps({"version": 1, "kind": "subscribe", "channel": "sum", "stream": "summary"}))
        await rt.send(
            json.dumps(
                {"version": 1, "kind": "subscribe", "channel": "foc", "stream": "focus", "root_thread_id": thread_id}
            )
        )
        await recv_until(rt, lambda f: f.get("channel") == "foc" and f["frame"]["kind"] == "snapshot")

        await draft_ws.send('{"api_key":"bench-key"}')
        joined = await recv_until(draft_ws, lambda f: f.get("kind") == "draft")
        doc = composer_document()
        doc.apply_update(base64.b64decode(joined["update_base64"]))
        prompt_text = f"Benchmark follow-up {uuid4().hex[:8]}"
        doc.get("text", type=Text).insert(0, prompt_text)
        update_b64 = base64.b64encode(doc.get_update()).decode()

        t_draft_start = perf_counter()
        await draft_ws.send(json.dumps({"kind": "sync", "draft_id": joined["draft_id"], "update_base64": update_b64}))
        synced = await recv_until(
            draft_ws,
            lambda f: f.get("kind") == "draft" and prompt_text in str(_decode_doc(f["update_base64"])["text"]),
        )
        draft_sync_ms = (perf_counter() - t_draft_start) * 1000

        source_id = f"input_{uuid4().hex}"
        t_submit_start = perf_counter()
        resp = await api.post(
            f"/api/threads/{thread_id}/submit",
            json={
                "parts": [prompt_text],
                "source_id": source_id,
                "draft": {
                    "draft_id": joined["draft_id"],
                    "update_base64": synced["update_base64"],
                    "options_update_base64": synced.get("options_update_base64"),
                },
            },
        )
        resp.raise_for_status()
        submit_ack_ms = (perf_counter() - t_submit_start) * 1000
        receipt_id = resp.json()["receipt_id"]

        final_text_at: float | None = None
        summary_terminal_at: float | None = None
        ui_settled_at: float | None = None
        ui_settled = asyncio.Event()
        focus_probe_task: asyncio.Task[dict[str, float]] | None = None
        bg_tasks: set[asyncio.Task[Any]] = set()

        async def browser_refetch(kind: str) -> None:
            nonlocal ui_settled_at
            await asyncio.sleep(0.025 if kind == "lifecycle" else 0.2)
            if kind in ("usage", "lifecycle"):
                await asyncio.gather(
                    api.get(f"/api/threads/{thread_id}/usage"),
                    api.get(f"/api/threads/{thread_id}/context-usage"),
                )
            if kind == "lifecycle":
                detail_r, op_r, _ = await asyncio.gather(
                    api.get(f"/api/threads/{thread_id}"),
                    api.get(f"/api/operations/{receipt_id}"),
                    api.get(f"/api/threads/{thread_id}/work"),
                )
                op_data = op_r.json()
                detail_data = detail_r.json()
                last_exec = detail_data["thread"].get("last_execution") or {}
                settled_exec = last_exec.get("execution_id") == receipt_id and last_exec.get("status") in (
                    "completed",
                    "failed",
                    "cancelled",
                    "suspended",
                )
                if (
                    detail_data["thread"]["root_activity"]["state"] == "inactive"
                    and (settled_exec or op_data["status"] in ("completed", "failed", "cancelled", "suspended"))
                    and ui_settled_at is None
                ):
                    ui_settled_at = perf_counter()
                    ui_settled.set()

        def spawn_refetch(kind: str) -> None:
            t = asyncio.create_task(browser_refetch(kind))
            bg_tasks.add(t)
            t.add_done_callback(bg_tasks.discard)

        async def read_rt() -> None:
            nonlocal final_text_at, summary_terminal_at, focus_probe_task
            while not ui_settled.is_set():
                raw = await rt.recv()
                now = perf_counter()
                msg = json.loads(raw)
                if msg.get("kind") == "ping":
                    await rt.send('{"version":1,"kind":"pong"}')
                    continue
                ch = msg.get("channel")
                fr = msg.get("frame", {})
                if ch == "foc" and fr.get("kind") == "event":
                    payload = fr["event"]["payload"]
                    ptype = payload.get("type")
                    if focus_probe_task is None and ptype in ("TOOL_CALL_START", "TEXT_MESSAGE_CONTENT"):
                        focus_probe_task = asyncio.create_task(measure_focus_subscribe(ws_url, http_url, thread_id))
                    if ptype == "TEXT_MESSAGE_CONTENT" and "Final synthetic answer" in payload.get("delta", ""):
                        final_text_at = now
                    elif ptype == "CUSTOM" and payload.get("name") == "a13n.harness_ui.root_settled":
                        if summary_terminal_at is None and final_text_at is not None:
                            summary_terminal_at = now
                        spawn_refetch("lifecycle")
                    elif ptype == "CUSTOM" and payload.get("name") in (
                        "a13n.harness.usage",
                        "a13n.harness_ui.checkpoint",
                    ):
                        spawn_refetch("usage")
                    elif ptype == "RUN_FINISHED":
                        spawn_refetch("lifecycle")
                elif ch == "sum" and fr.get("kind") == "invalidation":
                    ev = fr["event"]
                    if ev.get("kind") == "root_operation" and ev.get("root_thread_id") == thread_id:
                        if summary_terminal_at is None and final_text_at is not None and ev.get("notice") is not None:
                            summary_terminal_at = now
                        spawn_refetch("lifecycle")

        reader = asyncio.create_task(read_rt())
        try:
            await asyncio.wait_for(ui_settled.wait(), timeout=60.0)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        focus_cold = (
            await focus_probe_task
            if focus_probe_task is not None
            else await measure_focus_subscribe(ws_url, http_url, thread_id)
        )
        focus_expired = await measure_focus_subscribe(ws_url, http_url, thread_id, after=expired_cursor)

        op_final = (await api.get(f"/api/operations/{receipt_id}")).json()
        sub_dt = datetime.fromisoformat(op_final["receipt"]["submitted_at"])
        start_dt = datetime.fromisoformat(op_final["started_at"])
        end_dt = datetime.fromisoformat(op_final["completed_at"])
        assert final_text_at is not None
        return {
            "draft_sync_ms": draft_sync_ms,
            "submit_ack_ms": submit_ack_ms,
            "submitted_to_started_ms": (start_dt - sub_dt).total_seconds() * 1000,
            "started_to_ended_ms": (end_dt - start_dt).total_seconds() * 1000,
            "focus_first_frame_ms": focus_cold["first_frame_ms"],
            "focus_usable_output_ms": focus_cold["usable_output_ms"],
            "focus_expired_cursor_ms": focus_expired["usable_output_ms"],
            "final_text_to_summary_terminal_ms": max(
                0.0, ((summary_terminal_at or ui_settled_at) - final_text_at) * 1000
            ),
            "final_text_to_ui_settled_ms": max(0.0, (ui_settled_at - final_text_at) * 1000),
        }


def _decode_doc(b64: str) -> Doc:
    d = composer_document()
    d.apply_update(base64.b64decode(b64))
    return d


async def run_scenario(
    *,
    active_chats: int,
    rounds: int,
    total_chats: int,
    turns: int,
    steps_per_turn: int,
    payload_bytes: int,
    usage_scopes: int,
    records_per_scope: int,
    draft_edits: int,
    stagger_ms: float = 0.0,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="webui-latency-1778-") as tmp:
        root = Path(tmp)
        cfg = write_config(root)
        async with run_server(root, cfg) as (http_url, ws_url, app):
            threads = [await app.create_thread(title=f"Populated {idx}") for idx in range(total_chats)]
            seed_info = {}
            for th in threads:
                seed_info = await populate_thread(
                    app,
                    th.thread_id,
                    turns=turns,
                    steps_per_turn=steps_per_turn,
                    payload_bytes=payload_bytes,
                    usage_scopes=usage_scopes,
                    records_per_scope=records_per_scope,
                    draft_edits=draft_edits,
                )

            lags: list[float] = []

            async def lag_monitor() -> None:
                while True:
                    t0 = perf_counter()
                    await asyncio.sleep(0.005)
                    now = perf_counter()
                    lags.append(max(0.0, (now - t0 - 0.005) * 1000))

            monitor = asyncio.create_task(lag_monitor())
            samples: list[dict[str, float]] = []
            try:
                for _ in range(rounds):
                    batch = await asyncio.gather(
                        *(
                            exercise_turn(
                                http_url,
                                ws_url,
                                threads[idx].thread_id,
                                expired_cursor=base64.urlsafe_b64encode(
                                    json.dumps(
                                        {
                                            "kind": "focus",
                                            "root_thread_id": threads[idx].thread_id,
                                            "epoch": "expired-epoch",
                                            "sequence": 1,
                                        }
                                    ).encode()
                                ).decode(),
                                delay_seconds=(stagger_ms / 1000.0)
                                if (active_chats > 1 and idx == active_chats - 1)
                                else 0.0,
                            )
                            for idx in range(active_chats)
                        )
                    )
                    samples.extend(batch)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

            keys = list(samples[0].keys())
            return {
                "active_chats": active_chats,
                "rounds": rounds,
                "store_shape": seed_info,
                "stages": {k: stats([s[k] for s in samples]) for k in keys},
                "event_loop_lag": stats(lags),
            }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--total-chats", type=int, default=6)
    parser.add_argument("--turns", type=int, default=12)
    parser.add_argument("--steps-per-turn", type=int, default=6)
    parser.add_argument("--payload-bytes", type=int, default=16384)
    parser.add_argument("--usage-scopes", type=int, default=25)
    parser.add_argument("--records-per-scope", type=int, default=12)
    parser.add_argument("--draft-edits", type=int, default=60)
    parser.add_argument("--stagger-ms", type=float, default=150.0)
    args = parser.parse_args()

    for active in args.active:
        result = await run_scenario(
            active_chats=active,
            rounds=args.rounds,
            total_chats=max(args.total_chats, active),
            turns=args.turns,
            steps_per_turn=args.steps_per_turn,
            payload_bytes=args.payload_bytes,
            usage_scopes=args.usage_scopes,
            records_per_scope=args.records_per_scope,
            draft_edits=args.draft_edits,
            stagger_ms=args.stagger_ms,
        )
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
