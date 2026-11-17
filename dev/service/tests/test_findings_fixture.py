"""The scripted analyst consumes the production task and native, handle-based tool protocol."""

import json
from pathlib import Path

from a13n_service.runs.findings.inputs import AnalysisInput, Target
from a13n_service.runs.findings.schemas import FindingCreate

from dev.fixtures.findings import READ_SPANS, READ_TRACE, SUBMIT, selection, steps
from dev.fixtures.model import planned_tool

ROOT = Path(__file__).resolve().parents[3]
TRACE = "a" * 32
ROOT_SPAN = "b" * 16
CHILD = "c" * 16


def analysis_prompt(heading="A changed human-readable heading") -> str:
    task = AnalysisInput(
        analysis_id="fan_fixture",
        targets=[
            Target(
                trace_id=TRACE,
                run_id="run_11111111111111111111",
                agent_id="ap_22222222222222222222",
                agent_revision_id="apr_33333333333333333333",
                agent_name="Target",
                agent_url="/agents/ap_22222222222222222222",
                run_url="/runs/run_11111111111111111111",
                trace_url=f"/traces/{TRACE}",
            )
        ],
        presets={"execution": "Inspect errors."},
        requirements="Read child evidence.",
        selection_truncated=False,
        feedback={},
    )
    return f"Current Environment mounts (trusted dynamic context):\n{{}}\n{heading}\n{task.model_dump_json()}"


def exchange(messages, action, value):
    name, arguments = action
    call_id = f"call-{len(messages)}"
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}],
            },
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(value)},
        ]
    )


def test_analyst_reads_actual_handles_and_continuations_before_native_submission():
    prompt, messages = analysis_prompt(), []
    responses = [
        {"summary": {"id": ROOT_SPAN, "trace_id": TRACE}, "content": {"all": "opaque-root"}},
        {
            "fragment": {"text": '{"attributes":{"a13n.input":"[finding-', "has_more": True},
            "continuation": "opaque-next",
        },
        {"fragment": {"text": 'shipping]"}}', "has_more": False}, "continuation": None},
        {
            "items": [
                {"id": ROOT_SPAN, "kind": "agent"},
                {"id": "d" * 16, "kind": "span"},
                {"id": CHILD, "kind": "tool"},
            ],
            "next_cursor": None,
        },
        {"summary": {"id": CHILD, "trace_id": TRACE}, "content": {"all": "opaque-child"}},
        {"fragment": {"text": '{"output":"unverified price"}', "has_more": False}, "continuation": None},
    ]
    completed = []
    for response in responses:
        plan = steps(messages, prompt)
        assert plan[:-1] == completed
        action = plan[-1]
        completed.append(action)
        exchange(messages, action, response)
    plan = steps(messages, prompt)
    assert plan[:-1] == completed
    assert [name for name, _ in plan] == [
        READ_TRACE,
        "read_content",
        "read_content",
        READ_SPANS,
        "get_trace_span",
        "read_content",
        SUBMIT,
    ]
    assert plan[1][1] == {"reference": "opaque-root"}
    assert plan[2][1] == {"reference": "opaque-next"}
    assert plan[5][1] == {"reference": "opaque-child"}
    finding = FindingCreate.model_validate(plan[-1][1])
    assert finding.agent_revision_id == "apr_33333333333333333333"
    assert finding.evidence[0].span_ids == [ROOT_SPAN, CHILD]
    assert finding.source_key == f"fan_fixture:{TRACE}:shipping"
    body = {"messages": messages, "tools": [{"function": {"name": SUBMIT}}]}
    assert planned_tool(body, prompt)["function"]["name"] == SUBMIT
    assert steps(messages, analysis_prompt("Another heading")) == plan


def test_analyst_abstains_from_unread_and_unrecognized_content():
    prompt, messages = analysis_prompt(), []
    assert steps(messages, prompt) == [(READ_TRACE, {"trace_id": TRACE})]
    exchange(messages, steps(messages, prompt)[-1], {"summary": {"id": ROOT_SPAN}, "content": {"all": "opaque"}})
    exchange(
        messages, steps(messages, prompt)[-1], {"fragment": {"text": "Ordinary local prompt"}, "continuation": None}
    )
    exchange(messages, steps(messages, prompt)[-1], {"items": [], "next_cursor": None})
    assert SUBMIT not in [name for name, _ in steps(messages, prompt)]
    assert selection("Analysis fan_fixture. malformed selection") is None
    assert selection("Ordinary local prompt") is None


def test_analyst_tools_name_the_platform_operations():
    paths = json.loads((ROOT / "proto/a13n-service/openapi.json").read_text())["paths"]
    assert (READ_TRACE, READ_SPANS, SUBMIT) == (
        paths["/api/v1/traces/{trace_id}"]["get"]["operationId"],
        paths["/api/v1/traces/{trace_id}/spans"]["get"]["operationId"],
        paths["/api/v1/findings"]["post"]["operationId"],
    )
