"""Browser inspection reads original owned output without consuming model delivery."""

from datetime import UTC, datetime

import pytest
from a13n_harness import StoredToolExecution, ToolExecutionRecord
from a13n_harness_ui.errors import ThreadError
from anyio import Event, fail_after

from .test_child_checkpoints import transcript_client
from .test_dynamic_subagents import new_thread, open_app

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def model(messages, info):
    yield "unused"


async def test_browser_output_is_bounded_scoped_and_does_not_acknowledge(tmp_path, monkeypatch):
    async with await open_app(tmp_path, monkeypatch, model) as app:
        thread = await new_thread(app)
        other = await new_thread(app)
        record = ToolExecutionRecord(
            execution_id="exec-output",
            thread_id=thread.thread_id,
            run_id="run",
            tool_call_id="call",
            tool_name="review",
            status="succeeded",
            result_available=True,
            foreground_released=True,
            completed_at=datetime.now(UTC),
        )
        await app._store.tool_executions.save(StoredToolExecution(record, "retained result\n" * 6000))
        async with transcript_client(app) as client:
            response = await client.get(f"/api/threads/{thread.thread_id}/tool-executions/exec-output/output")
            assert response.status_code == 200
            output = response.json()
            assert output["truncated"] and len(output["text"]) == 65536 and output["state"] == "available"
            assert not output["execution"]["delivery_acknowledged"]
            assert not (
                await app._store.tool_executions.get(thread.thread_id, record.execution_id)
            ).record.delivery_acknowledged
        with pytest.raises(ThreadError, match="unavailable"):
            await app.tool_execution_output(other.thread_id, record.execution_id)


async def test_unavailable_result_is_explicit_and_live_shell_only_reads_original_resource(tmp_path, monkeypatch):
    async with await open_app(tmp_path, monkeypatch, model) as app:
        thread = await new_thread(app)
        record = ToolExecutionRecord(
            execution_id="exec-expired",
            thread_id=thread.thread_id,
            run_id="run",
            tool_call_id="call",
            tool_name="review",
            status="lost",
        )
        await app._store.tool_executions.save(StoredToolExecution(record))
        assert (await app.tool_execution_output(thread.thread_id, record.execution_id)).state == "unavailable"
        from datetime import timedelta

        from a13n_harness_ui.storage.database import transaction
        from a13n_harness_ui.storage.models import ToolExecutionHeadRecord
        from sqlalchemy import update

        async with transaction(app._store.tool_executions.sessions) as session:
            await session.execute(
                update(ToolExecutionHeadRecord)
                .where(ToolExecutionHeadRecord.execution_id == record.execution_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert (await app.tool_execution_output(thread.thread_id, record.execution_id)).state == "expired"
        bounded = record.model_copy(
            update={"execution_id": "exec-bounded", "status": "succeeded", "result_available": True}
        )
        await app._store.tool_executions.save(
            StoredToolExecution(
                bounded,
                {"result": "bounded preview", "truncated": True, "output_chars": 100000, "output_bytes": 100000},
            )
        )
        preview = await app.tool_execution_output(thread.thread_id, bounded.execution_id)
        assert preview.truncated and preview.text == "bounded preview"

        entered, finish = Event(), Event()
        requests = []
        runtime = app._tool_executions.runtime

        class Shell:
            async def call(self, request):
                requests.append(request)
                return {"stdout": "current live tail", "status": {"phase": "running"}}

        async def operation():
            runtime.attach_resource(Shell())
            entered.set()
            await finish.wait()
            return "completed original invocation"

        live = runtime.admit(
            operation, thread_id=thread.thread_id, run_id="run", tool_call_id="shell", tool_name="shell_exec"
        )
        try:
            with fail_after(10):
                await entered.wait()
            output = await app.tool_execution_output(thread.thread_id, live.execution_id)
            assert output.live and "current live tail" in output.text
            assert requests == [{"operation": "wait", "timeout_seconds": 0}]
        finally:
            finish.set()
            await runtime.wait(thread.thread_id, live.execution_id, timeout_seconds=10)
        output = await app.tool_execution_output(thread.thread_id, live.execution_id)
        assert not output.live and output.text == "completed original invocation"
