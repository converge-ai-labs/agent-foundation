from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness import RunBindings
from a13n_harness._review_context import REVIEW_HISTORY_ID, ReviewHistory
from a13n_harness.capabilities import AgentToolReviewer, ToolReviewAssessment, ToolReviewConfig, ToolReviewError
from a13n_harness.providers.model import routes
from a13n_harness.providers.model.credentials import ApiKeyCredential
from pydantic import ValidationError
from pydantic_ai.models.typesafe import TypeSafeModel

from .test_tool_review import _build

pytestmark = pytest.mark.anyio


async def _allow_endpoint(self, endpoint):
    return endpoint


def _wire_model(monkeypatch, handler):
    monkeypatch.setattr(routes.EndpointPolicy, "validate", _allow_endpoint)
    monkeypatch.setattr(
        routes, "create_model_http_client", lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    return routes.build_api_key_model("typesafe:jev-latest", ApiKeyCredential(api_key="fixture"))


def _answer(score, confidence=0.9):
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 30, "output_tokens": 1},
        "answers": {
            "severity": {
                "type": "score",
                "score": score,
                "confidence": confidence,
                "legend": {str(i): name for i, name in enumerate(("low", "medium", "high", "extra_high"))},
                "probabilities": {"0": 0.1, "1": 0.1, "2": 0.4, "3": 0.4},
            }
        },
    }


# One score per grade: Pydantic AI owns score rounding; confidence alternates to show it never changes the grade.
@pytest.mark.parametrize(
    ("score", "risk", "confidence"),
    [
        (0.49, "low", 0.99),
        (0.5, "medium", 0.01),
        (2.49, "high", 0.99),
        (2.5, "extra_high", 0.01),
    ],
)
async def test_native_jev_score_maps_to_risk_without_text_or_confidence_policy(
    reviewer_context, monkeypatch, score, risk, confidence
):
    from a13n_harness.capabilities import ToolReviewRequest

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer fixture"
        return httpx2.Response(200, json=_answer(score, confidence))

    model = await _wire_model(monkeypatch, respond)
    assert isinstance(model, TypeSafeModel)
    assert model.profile["supports_text_output"] is False
    async with model:
        result = await AgentToolReviewer(
            model,
            ToolReviewConfig(
                model="model-jev", instruction="General custom rule", shell_instruction="Shell custom rule"
            ),
        ).review(
            ToolReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="call-1",
                tool_name="shell_exec",
                profile="shell",
                parameters_schema={"type": "object"},
                arguments={"command": "printf safe"},
            ),
            context=reviewer_context,
        )
    assert result.assessment == ToolReviewAssessment(risk=risk)
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {"severity"}
    assert body["questions"]["severity"]["type"] == "score"
    questions = json.dumps(body["questions"])
    state = json.dumps(body["state"])
    assert "Shell custom rule" in questions and "General custom rule" not in questions
    assert "credential exfiltration" in questions
    assert "Shell custom rule" not in state and "Default shell risk criteria" not in state
    assert "printf safe" in state
    assert result.usage[0].provider == "typesafe"
    assert {item.unit: item.quantity for item in result.usage[0].measures}["requests"] == 1


@pytest.mark.parametrize(
    ("score", "on_flagged", "status", "executed"),
    [
        (0, "approval_required", "completed", True),
        (2, "approval_required", "suspended", False),
        (3, "deny", "completed", False),
    ],
)
async def test_scored_review_preserves_permission_decisions_and_nullable_history(
    monkeypatch, score, on_flagged, status, executed
):
    model = await _wire_model(monkeypatch, lambda request: httpx2.Response(200, json=_answer(score)))
    calls = []
    async with model:
        reviewer = AgentToolReviewer(model, ToolReviewConfig(model="model-jev"))
        result = await _build(reviewer, calls, on_flagged=on_flagged).run(
            "Run the check", bindings=RunBindings.embedded()
        )
    assert result.status == status
    assert bool(calls) is executed
    entry = result.state.agent_context_state.entries[REVIEW_HISTORY_ID]
    history = ReviewHistory.model_validate(entry.data)
    reviews = [record for record in history.records if record.kind == "review"]
    assert reviews[-1].reason is None
    if status == "suspended":
        assert result.deferred.metadata["shell-call-1"]["a13n.harness.tool-review"] == {"risk": "high", "reason": None}
    assert ReviewHistory.model_validate_json(history.model_dump_json()) == history


@pytest.mark.parametrize("response", [httpx2.Response(500), httpx2.Response(200, json={"invalid": True})])
async def test_jev_provider_failures_are_bounded_review_errors(reviewer_context, monkeypatch, response):
    from a13n_harness.capabilities import ToolReviewRequest

    calls = []

    def respond(request):
        calls.append(request)
        return response

    model = await _wire_model(monkeypatch, respond)
    async with model:
        with pytest.raises(ToolReviewError, match="tool_review_failed"):
            await AgentToolReviewer(model, ToolReviewConfig(model="model-jev")).review(
                ToolReviewRequest(
                    tool_id="tool", tool_call_id="call", tool_name="tool", parameters_schema={}, arguments={}
                ),
                context=reviewer_context,
            )
    assert len(calls) == 1


@pytest.mark.parametrize("reason", ["  ", "bad\x00text", "x" * 2001])
def test_present_review_reason_still_validated(reason):
    with pytest.raises(ValidationError):
        ToolReviewAssessment(risk="low", reason=reason)
