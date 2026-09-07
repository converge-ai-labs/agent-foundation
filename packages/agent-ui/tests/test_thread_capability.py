from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from a13n_ui.thread_capability import ThreadCollaborationCapability

pytestmark = pytest.mark.anyio


class _Controller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_threads(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        return {"threads": [], "total": 0, "next_cursor": None}

    async def get_thread(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(("get", kwargs))
        return {"thread": {"thread_id": kwargs["thread_id"]}, "transcript": {"entries": []}}

    async def run_thread(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(("run", kwargs))
        return {"receipt_id": "receipt-1"}

    async def steer_thread(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(("steer", kwargs))
        return {"accepted": True, "receipt_id": "receipt-1", "enqueue_id": "enqueue-1"}


async def test_thread_capability_uses_only_detached_controller_and_preserves_source_scope() -> None:
    controller: Any = _Controller()
    capability = ThreadCollaborationCapability(
        controller=controller,
        source_thread_id="thread-source",
    )
    context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-source"))

    listed = await capability.list_threads(context, query="test", cursor=None, limit=5)
    assert listed == {"ok": True, "threads": [], "total": 0, "next_cursor": None}

    recursive = await capability.run_thread(context, thread_id="thread-source", prompt="again")
    assert recursive["ok"] is False
    assert recursive["error"]["code"] == "thread_recursive_run"

    run = await capability.run_thread(context, thread_id="thread-target", prompt="work")
    assert run == {"ok": True, "receipt": {"receipt_id": "receipt-1"}}
    steer = await capability.steer_thread(context, thread_id="thread-target", message="focus")
    assert steer["ok"] is True
    assert steer["receipt_id"] == "receipt-1"
    assert [name for name, _kwargs in controller.calls] == ["list", "run", "steer"]

    toolset = capability.get_toolset()
    assert toolset.id == "a13n-agent-ui-thread-tools"


async def test_thread_capability_rejects_reuse_in_a_child_scope() -> None:
    from a13n_harness.errors import DefinitionError

    controller: Any = _Controller()
    capability = ThreadCollaborationCapability(controller=controller, source_thread_id="thread-root")
    context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-child"))
    with pytest.raises(DefinitionError, match="scope"):
        await capability.list_threads(context, limit=5)
    assert controller.calls == []
    tools = capability.get_toolset().tools
    assert "create_thread" in tools
    assert "project_id" not in tools["create_thread"].function_schema.json_schema.get("properties", {})
