from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import SafeFailure
from a13n_ui.environment_runtime import WorkspaceBinding
from a13n_ui.session_capability import AgentUiSessionCapability
from a13n_ui.session_service import SessionService
from pydantic_ai import RunContext

pytestmark = pytest.mark.anyio


class _FakeSessionService:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.sessions = (
            SimpleNamespace(
                session_id="session-new",
                title="New work",
                created_at=now,
                updated_at=now,
                archived_at=None,
                pinned=True,
                status="active",
            ),
            SimpleNamespace(
                session_id="session-old",
                title="Old work",
                created_at=now,
                updated_at=now,
                archived_at=now,
                pinned=False,
                status="archived",
            ),
        )
        self.run_folders: tuple[Path, ...] | None = None
        self.recursive_call_attempted = False

    async def list(self, *, query: str | None, offset: int, limit: int):
        selected = self.sessions
        if query:
            selected = tuple(item for item in selected if query.casefold() in item.title.casefold())
        return selected[offset : offset + limit], len(selected)

    async def inspect(self, *, session_id: str, history_offset: int, history_limit: int):
        session = next(item for item in self.sessions if item.session_id == session_id)
        history = [
            {"kind": "request", "parts": [{"content": "first"}]},
            {"kind": "response", "parts": [{"content": "second"}]},
        ]
        return session, history[history_offset : history_offset + history_limit], len(history)

    async def run(self, *, session_id: str, prompt: str, folders: tuple[Path, ...]):
        del session_id, prompt
        self.run_folders = folders
        return SimpleNamespace(
            result=SimpleNamespace(
                status="completed",
                output="finished",
                failure=cast(SafeFailure | None, None),
            ),
            continuation=SimpleNamespace(status="selected"),
        )

    async def steer(self, *, session_id: str, message: str):
        del message
        return SimpleNamespace(
            session_id=session_id,
            accepted=True,
            enqueue_id="enqueue-1",
        )


async def test_session_capability_pages_bounded_list_and_history(tmp_path: Path) -> None:
    service = _FakeSessionService()
    capability = _capability(service, tmp_path)

    first = await capability.list_sessions(_context(), limit=1)
    assert first["ok"] is True
    assert [item["session_id"] for item in first["sessions"]] == ["session-new"]
    assert first["next_cursor"] is not None

    second = await capability.list_sessions(_context(), cursor=first["next_cursor"], limit=1)
    assert [item["session_id"] for item in second["sessions"]] == ["session-old"]
    assert second["next_cursor"] is None

    history = await capability.get_session(_context(), session_id="session-new", history_limit=1)
    assert history["ok"] is True
    assert len(history["history"]) == 1
    assert history["next_history_cursor"] is not None

    invalid = await capability.list_sessions(_context(), cursor="not-a-cursor")
    assert invalid == {
        "ok": False,
        "error": {"code": "session_list_failed", "message": "Session cursor is invalid"},
    }


async def test_session_capability_rejects_recursive_control_and_inherits_binding(tmp_path: Path) -> None:
    service = _FakeSessionService()
    capability = _capability(service, tmp_path)

    recursive_run = await capability.run_session(
        _context(),
        session_id="session-root",
        prompt="recurse",
    )
    recursive_steer = await capability.steer_session(
        _context(),
        session_id="session-root",
        message="recurse",
    )
    assert recursive_run["error"]["code"] == "session_recursive_run"
    assert recursive_steer["error"]["code"] == "session_recursive_steer"

    outcome = await capability.run_session(
        _context(),
        session_id="session-other",
        prompt="continue",
    )
    assert outcome["ok"] is True
    assert outcome["continuation_status"] == "selected"
    assert service.run_folders == (tmp_path,)


def _capability(service: _FakeSessionService, folder: Path) -> AgentUiSessionCapability:
    return AgentUiSessionCapability(
        service=cast(SessionService, service),
        source_session_id="session-root",
        binding=WorkspaceBinding(folders=(folder,)),
    )


def _context() -> RunContext[Any]:
    return cast(RunContext[Any], None)
