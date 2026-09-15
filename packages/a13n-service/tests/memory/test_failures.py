import asyncio

import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend
from a13n_service.application_errors import ApplicationError
from a13n_service.memory.domain import MemoryScope, MemoryWrite
from pydantic import ValidationError

from ..models.conftest import WORKSPACE_ID, actor
from .support import memory_service
from .test_api import native_transport


@pytest.mark.parametrize("text", ["", " \n\t "])
def test_blank_write_is_rejected_without_trimming_valid_text(text):
    with pytest.raises(ValidationError):
        MemoryWrite(text=text)
    assert MemoryWrite(text="  valid text  ").text == "  valid text  "


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["add", "update", "delete"])
async def test_committed_but_unconfirmed_write_is_not_retried(memory_sessions, operation):
    records, calls = {}, []
    transport = native_transport(records, calls, httpx2.Response)
    fail = False

    async def handler(request):
        response = transport(request)
        if fail and request.method in {"POST", "PUT", "DELETE"}:
            raise httpx2.ReadTimeout("private provider detail", request=request)
        return response

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handler)) as client:
        service, provider, _ = await memory_service(memory_sessions, Mem0OSSBackend(client))
        kwargs = {
            "actor": actor(),
            "workspace_id": WORKSPACE_ID,
            "provider_id": provider.id,
            "selection": MemoryScope(scope="user"),
        }
        existing = await service.add(**kwargs, text="before")
        before = len(calls)
        fail = True
        with pytest.raises(ApplicationError) as caught:
            if operation == "add":
                await service.add(**kwargs, text="after")
            elif operation == "update":
                await service.update(**kwargs, memory_id=existing.id, text="after")
            else:
                await service.delete(**kwargs, memory_id=existing.id)
        assert caught.value.code == "memory_write_unconfirmed"
        assert "private" not in str(caught.value)
        mutations = [call for call in calls[before:] if call[0] != "GET"]
        assert len(mutations) == 1
        if operation == "delete":
            assert existing.id not in records
        else:
            assert "after" in {record["memory"] for record in records.values()}


@pytest.mark.anyio
async def test_mutation_deadline_includes_scope_read_and_verification(memory_sessions):
    records, calls = {}, []
    transport = native_transport(records, calls, httpx2.Response)
    block = False

    async def handler(request):
        if block:
            await asyncio.sleep(0.04)
        return transport(request)

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handler)) as client:
        service, provider, _ = await memory_service(memory_sessions, Mem0OSSBackend(client), timeout=0.06)
        kwargs = {
            "actor": actor(),
            "workspace_id": WORKSPACE_ID,
            "provider_id": provider.id,
            "selection": MemoryScope(scope="user"),
        }
        existing = await service.add(**kwargs, text="before")
        block = True
        with pytest.raises(ApplicationError) as caught:
            await service.update(**kwargs, memory_id=existing.id, text="after")
        assert caught.value.code == "memory_write_unconfirmed"
        assert records[existing.id]["memory"] == "before"


def test_native_thread_id_is_a_valid_management_subject():
    assert MemoryScope(scope="thread", subject_id="thread-" + "a" * 32).subject_id == "thread-" + "a" * 32
