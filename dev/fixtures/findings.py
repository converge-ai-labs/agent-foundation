"""Fictional Findings scenarios and a deterministic analyst for the local scripted model.

The analyst reads the selected real traces before submitting through the Finding Agent's platform tools. It
recognizes only these explicit preview markers; other local traces receive no canned diagnosis.
"""

from __future__ import annotations

import json
from typing import Any

# The platform tools of the trace reads and the finding submission, named after their operation IDs.
READ_TRACE = "get_trace"
READ_SPANS = "list_trace_spans"
SUBMIT = "create_finding"

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


def selection(prompt: str) -> dict | None:
    """Find the typed production task among prepended Service context and user content."""
    decoder = json.JSONDecoder()
    for offset, char in enumerate(prompt):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(prompt[offset:])
        except ValueError:
            continue
        if (
            isinstance(value, dict)
            and value.get("kind") == "finding_analysis"
            and isinstance(value.get("targets"), list)
        ):
            return value
    return None


def steps(messages: list[dict], prompt: str) -> list[tuple[str, dict]]:
    task = selection(prompt)
    if task is None:
        return []
    # Match native tool results to their actual calls, not HTTP envelopes or invented handles.
    calls = {call["id"]: call["function"] for message in messages for call in message.get("tool_calls", [])}
    results: dict[tuple[str, str], dict] = {}
    for message in messages:
        call = calls.get(message.get("tool_call_id"))
        if message.get("role") != "tool" or call is None:
            continue
        try:
            value = json.loads(message.get("content") or "null")
            arguments = json.loads(call["arguments"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            results[(call["name"], json.dumps(arguments, sort_keys=True))] = value
    plan: list[tuple[str, dict]] = []

    def read(name: str, arguments: dict) -> dict | None:
        plan.append((name, arguments))
        return results.get((name, json.dumps(arguments, sort_keys=True)))

    def content(reference: str) -> str | None:
        fragments = []
        while reference:
            result = read("read_content", {"reference": reference})
            if result is None:
                return None
            fragment = result.get("fragment", {})
            if not isinstance(fragment.get("text"), str):
                return None
            fragments.append(fragment["text"])
            reference = result.get("continuation")
        return "".join(fragments)

    # Stop at the first unanswered call. Discovered steps extend a stable completed prefix.
    for target in task["targets"]:
        trace_id = target["trace_id"]
        root = read(READ_TRACE, {"trace_id": trace_id})
        if root is None:
            return plan
        reference = root.get("content", {}).get("all")
        if not reference:
            continue
        text = content(reference)
        if text is None:
            return plan
        span_ids = [root["summary"]["id"]]
        cursor = None
        while True:
            page = read(READ_SPANS, {"trace_id": trace_id, **({"cursor": cursor} if cursor else {})})
            if page is None:
                return plan
            for span in page.get("items", []):
                # The fixture diagnoses tool results, not every instrumentation span.
                if span["id"] == root["summary"]["id"] or span.get("kind") != "tool":
                    continue
                child = read("get_trace_span", {"trace_id": trace_id, "span_id": span["id"]})
                if child is None:
                    return plan
                reference = child.get("content", {}).get("all")
                if not reference:
                    continue
                child_text = content(reference)
                if child_text is None:
                    return plan
                text += child_text
                span_ids.append(span["id"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        for key, example in EXAMPLES.items():
            if f"[finding-{key}]" not in text:
                continue
            finding: dict[str, Any] = {
                **{name: example[name] for name in ("title", "category", "severity", "explanation", "suggestion")},
                **{name: target[name] for name in ("agent_id", "agent_revision_id")},
                "evidence": [{"trace_id": trace_id, "run_id": target["run_id"], "span_ids": span_ids}],
                "limitations": "Fictional local scripted example; one execution is cited. This deterministic preview is not a model-quality benchmark.",
                "source_key": f"{task['analysis_id']}:{trace_id}:{key}",
            }
            if read(SUBMIT, finding) is None:
                return plan
    return plan
