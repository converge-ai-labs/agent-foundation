"""Fictional Findings scenarios and a deterministic analyst for the local scripted model.

The analyst reads the selected real traces before submitting through ordinary builtin tools. It recognizes
only these explicit preview markers; other local traces receive no canned diagnosis.
"""

from __future__ import annotations

import json
import re
from typing import Any

EXAMPLES = {
    "shipping": {
        "prompt": "[finding-shipping] Can you send this overseas urgently? What would it cost?",
        "reply": "Your international express shipment is confirmed at $12, from New York to Los Angeles. No further details are needed.",
        "title": "Shipping assistant quotes a verified international express price for an unspecified package while silently assuming a domestic destination and omitting the clarification needed to fulfill the request",
        "category": "answer_quality",
        "severity": "critical",
        "explanation": "The user requested **urgent overseas shipping**, without giving a destination, weight or package details.\n\nThe reply claimed a confirmed $12 quote and named New York → Los Angeles. That route is domestic, and no booking or pricing tool was called.\n\n- Missing shipment details were silently assumed.\n- The service and destination conflict with the user's request.\n- A simulated quote was presented as verified.",
        "suggestion": "Ask for the **destination, weight and delivery deadline** before quoting.\n\n1. Confirm the requested service.\n2. Obtain a price from a configured pricing source.\n3. Describe any assumptions and avoid claiming a shipment is booked before an order tool succeeds.",
    },
    "endpoint": {
        "prompt": "[finding-endpoint] [mcp-fail] Look up the release review before recommending whether we can publish.",
        "reply": "The review lookup failed. I cannot verify release readiness, so I have not recommended publishing.",
        "title": "Release review lookup fails at the configured tool connection, leaving the publishing prerequisite unavailable despite a correctly bounded and honestly reported execution failure",
        "category": "tool_execution",
        "severity": "warning",
        "explanation": "The configured `fail_local_review` tool returned an error. The assistant could not retrieve the review needed for the publishing decision.\n\nThe final reply reported the failure and made no success claim. The actionable issue is the unavailable dependency, rather than fabricated results or an uncontrolled retry loop.",
        "suggestion": "Check the tool connection and the selected operation. Keep the publishing decision blocked until the prerequisite review is available.\n\n- Test the intended lookup tool.\n- Provide a user-visible recovery step when the dependency fails.\n- Preserve the existing honest failure response.",
    },
    "readonly": {
        "prompt": "[finding-readonly] [mcp] Look up the release review without changing the draft.",
        "reply": "The read-only review lookup completed. It did not change the draft or publish anything.",
        "title": "Review lookup may have modified the release draft without approval — an intentionally incorrect diagnosis for demonstrating a reviewer correction and the independent closed state",
        "category": "boundary_violation",
        "severity": "suggestion",
        "explanation": "The initial scripted diagnosis interprets a successful review lookup as a possible draft modification and suggests adding an approval gate.\n\nThis is deliberately incorrect: the evidence contains a **read-only lookup** and no draft write. Reviewers can record a false positive and explain the misunderstanding without changing this original diagnosis.",
        "suggestion": "The initial diagnosis proposes an approval gate before lookup. Verify the operation's semantics and cited tool result before accepting that proposal; the seeded reviewer correction explains why it is unnecessary.",
    },
}


def selection(prompt: str) -> tuple[list[dict], dict] | None:
    """Only the ordinary managed-analysis message has both selection and exact target revisions."""
    if not re.search(r"(?:^|\n)Analysis fan_", prompt):
        return None
    decoder = json.JSONDecoder()
    try:
        selected, _ = decoder.raw_decode(prompt.split("Selected traces: ", 1)[1])
        targets, _ = decoder.raw_decode(prompt.split("Target Agent revisions by trace: ", 1)[1])
    except (IndexError, ValueError):
        return None
    return selected, targets


def steps(messages: list[dict], prompt: str) -> list[tuple[str, dict]]:
    parsed = selection(prompt)
    if parsed is None:
        return []
    selected, targets = parsed
    analysis_id = re.search(r"Analysis (fan_\w+)", prompt)
    assert analysis_id is not None
    reads = [("read_trace", {"trace_id": item["trace_id"]}) for item in selected]
    reads += [("read_trace_spans", {"trace_id": item["trace_id"]}) for item in selected]
    roots: dict[str, dict] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            value = json.loads(message.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and "trace_id" in value and "attributes" in value:
            roots[value["trace_id"]] = value
    submissions = []
    for item in selected:
        trace_id = item["trace_id"]
        root = roots.get(trace_id)
        if root is None:
            continue
        inputs = json.dumps(root["attributes"].get("a13n.input", []))
        for key, example in EXAMPLES.items():
            if f"[finding-{key}]" not in inputs:
                continue
            target = targets[trace_id]
            finding: dict[str, Any] = {
                **{name: example[name] for name in ("title", "category", "severity", "explanation", "suggestion")},
                **target,
                "evidence": [{**item, "span_ids": [root["id"]]}],
                "limitations": "Fictional local scripted example; one selected execution is cited. This is deterministic preview data, not a model-quality benchmark.",
                "source_key": f"{analysis_id[1]}:{key}",
            }
            submissions.append(("submit_finding", {"finding": finding}))
    return reads + submissions
