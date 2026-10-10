"""The scripted analyst must use read evidence, even with Service context prepended."""

import json

from dev.fixtures.findings import selection, steps
from dev.fixtures.model import planned_tool


def analysis_prompt() -> str:
    return (
        "Current Environment mounts (trusted dynamic context):\n{}\n"
        'Analysis fan_fixture. Selected traces: [{"trace_id":"trace-real","run_id":"run_real"}].\n'
        "Target Agent revisions by trace: "
        '{"trace-real":{"agent_id":"ap_target","agent_revision_id":"apr_pinned"}}\n'
        "Inspect only the selected traces."
    )


def test_analyst_reads_before_submitting_and_cites_returned_evidence() -> None:
    prompt = analysis_prompt()
    reads = steps([], prompt)
    assert [name for name, _ in reads] == ["read_trace", "read_trace_spans"]
    body = {"messages": [], "tools": [{"function": {"name": "read_trace"}}]}
    first = planned_tool(body, prompt)
    assert isinstance(first, dict) and first["function"]["name"] == "read_trace"
    root = {"trace_id": "trace-real", "id": "span-returned", "attributes": {"a13n.input": ["[finding-shipping]"]}}
    plan = steps([{"role": "tool", "content": json.dumps(root)}], prompt)
    name, submitted = plan[-1]
    assert name == "submit_finding"
    finding = submitted["finding"]
    assert finding["agent_revision_id"] == "apr_pinned"
    assert finding["evidence"] == [{"trace_id": "trace-real", "run_id": "run_real", "span_ids": ["span-returned"]}]
    assert finding["source_key"] == "fan_fixture:shipping"


def test_analyst_abstains_from_unrecognized_and_unread_traces() -> None:
    root = {"trace_id": "other-trace", "id": "span-other", "attributes": {"a13n.input": ["[finding-shipping]"]}}
    assert len(steps([{"role": "tool", "content": json.dumps(root)}], analysis_prompt())) == 2
    root["trace_id"] = "trace-real"
    root["attributes"]["a13n.input"] = ["Ordinary local prompt"]
    assert len(steps([{"role": "tool", "content": json.dumps(root)}], analysis_prompt())) == 2
    assert selection("Analysis fan_fixture. malformed selection") is None
    assert selection("Ordinary local prompt") is None
