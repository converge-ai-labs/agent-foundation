from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability

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
    assert toolset.id == "a13n-a13n-harness-ui-thread-tools"


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
    assert "project_id" in tools["create_thread"].function_schema.json_schema.get("properties", {})
    assert {"list_projects", "get_project", "list_agents", "list_models", "send_thread_message"} <= tools.keys()


async def test_thread_tools_are_built_once_per_capability_without_sharing_root_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from a13n_harness.errors import DefinitionError
    from a13n_harness_ui import thread_capability

    constructed: list[str] = []
    original = thread_capability._tool

    def build_tool(function: Any, **kwargs: Any) -> Any:
        constructed.append(kwargs["name"])
        return original(function, **kwargs)

    monkeypatch.setattr(thread_capability, "_tool", build_tool)
    controller: Any = _Controller()
    first = ThreadCollaborationCapability(controller=controller, source_thread_id="thread-first")
    second = ThreadCollaborationCapability(controller=controller, source_thread_id="thread-second")
    first_tools = first.get_toolset()
    # Native Agent construction and Run capability resolution both ask for this
    # static surface. Neither changes this capability's captured root binding.
    again = first.get_toolset()
    assert len(constructed) == len(first_tools.tools)
    assert again is first_tools
    second_tools = second.get_toolset()
    assert second_tools is not first_tools
    assert len(constructed) == 2 * len(first_tools.tools)
    for name in first_tools.tools:
        assert first_tools.tools[name] is not second_tools.tools[name]
        assert first_tools.tools[name].function_schema is not second_tools.tools[name].function_schema

    first_context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-first"))
    second_context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-second"))
    await again.tools["list_threads"].function_schema.call({"limit": 5}, first_context)
    await second_tools.tools["list_threads"].function_schema.call({"limit": 7}, second_context)
    assert [kwargs["source_thread_id"] for _, kwargs in controller.calls] == ["thread-first", "thread-second"]
    with pytest.raises(DefinitionError, match="scope"):
        await again.tools["list_threads"].function_schema.call({"limit": 5}, second_context)
    assert len(controller.calls) == 2
