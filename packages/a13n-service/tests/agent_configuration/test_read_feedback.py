"""Read tools preserve useful diagnostics without exposing inaccessible resources."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.agent_configuration import runtime
from a13n_service.agent_configuration.runtime import ConfigurationCapability
from a13n_service.application_errors import ApplicationError, ErrorCategory
from pydantic_ai import ModelRetry


@pytest.fixture
def capability(monkeypatch):
    capability = object.__new__(ConfigurationCapability)
    capability._authorize = AsyncMock(
        return_value=(SimpleNamespace(authorization=SimpleNamespace(snapshot=None)), "invocation")
    )
    capability._actor = None
    capability._resources = SimpleNamespace(search=AsyncMock())
    capability._queries = SimpleNamespace(get_run=AsyncMock(), items=AsyncMock())
    capability._sessions = None
    capability._binding = SimpleNamespace(session_id="session")

    @asynccontextmanager
    async def session(_):
        yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(session_id="session")))

    monkeypatch.setattr(runtime, "short_session", session)
    return capability


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["search", "run"])
@pytest.mark.parametrize("code", ["invalid_cursor", "configuration_not_found"])
async def test_read_errors_distinguish_bad_cursor_from_concealed_resource(capability, tool, code):
    error = ApplicationError(code, "private-secret", category=ErrorCategory.invalid_request)
    if tool == "search":
        capability._resources.search.side_effect = error
        call = capability.search_configuration_resources(None, kind="model", cursor="bad")
    else:
        capability._queries.items.side_effect = error
        call = capability.read_interaction_run(None, run_id="run", cursor="bad")
    with pytest.raises(ModelRetry) as caught:
        await call
    feedback = str(caught.value)
    assert "private-secret" not in feedback
    if code == "invalid_cursor":
        assert json.loads(feedback)["code"] == "invalid_cursor"
        assert "Omit cursor" in feedback
    else:
        assert "unavailable or unauthorized" in feedback


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        {
            "code": "agent_run_failed",
            "message": "Execution failed.",
            "retry_hint": "new_run",
            "details": {"headers": "private-secret"},
        },
    ],
)
async def test_run_read_includes_safe_failure_or_null(capability, failure):
    capability._queries.get_run.return_value = SimpleNamespace(
        id="run",
        status=SimpleNamespace(value="failed" if failure else "succeeded"),
        failure=failure,
        input_text=None,
        output_text=None,
    )
    capability._queries.items.return_value = SimpleNamespace(model_dump=lambda **_: {"items": [], "next_cursor": None})
    result = await capability.read_interaction_run(None, run_id="run")
    if failure:
        assert result["failure"]["code"] == "agent_run_failed"
        assert result["failure"]["retry_hint"] == "new_run"
        assert "private-secret" not in json.dumps(result)
    else:
        assert result["failure"] is None
