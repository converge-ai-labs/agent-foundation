from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai import Agent

pytestmark = pytest.mark.anyio

_DEMO_PATH = Path(__file__).resolve().parents[3] / "dev" / "observation-demo" / "agent.py"


@pytest.mark.parametrize(
    ("scenario", "output", "events", "nested_path", "usage_records"),
    [
        (
            "summary",
            "Explicit summarize restored the continuation and completed the task.",
            ("handoff_started", "handoff_prepared", "handoff_completed"),
            ("invoke_agent summary-observation-demo", "execute_tool summarize", "handoff"),
            2,
        ),
        (
            "compaction",
            "Automatic compaction replaced the long history and completed the task.",
            ("context_snapshot", "compaction_started", "compaction_completed"),
            (
                "invoke_agent compaction-observation-demo",
                "chat compaction-demo-model",
                "compaction",
                "invoke_agent compaction-observation-demo",
                "chat compaction-demo-model",
            ),
            2,
        ),
        (
            "view",
            "View completed through the media-understanding Agent: Detected text: OBSERVATION DEMO. "
            "Unclear or omitted details: none.",
            ("environment_changed",),
            (
                "invoke_agent view-observation-demo",
                "execute_tool view",
                "invoke_agent image-understanding",
                "chat observation-vision-model",
            ),
            3,
        ),
        (
            "subagent",
            "Inline reviewer completed and the parent accepted its bounded report.",
            (),
            (
                "invoke_agent subagent-observation-demo",
                "execute_tool delegate",
                "delegation",
                "harness.run",
                "invoke_agent reviewer-observation-demo",
                "chat subagent-child-demo-model",
            ),
            2,
        ),
    ],
)
async def test_observation_demo_exports_complete_trace(
    scenario: str,
    output: str,
    events: tuple[str, ...],
    nested_path: tuple[str, ...],
    usage_records: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo = runpy.run_path(str(_DEMO_PATH))
    # The executable Host enables instrumentation for its internal media Agent.
    # Restore that process-wide default after each scenario, including failures.
    monkeypatch.setattr(Agent, "_instrument_default", Agent._instrument_default)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    presentation = demo["_ObservationPresentationProcessor"]()
    provider.add_span_processor(presentation)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        result = await demo["_run_scenario"](scenario, provider, presentation)
        assert result.result.output_or_raise() == output
        assert result.context_events == events
        assert len(result.result.usage_records) == usage_records
        assert provider.force_flush()
        spans = exporter.get_finished_spans()
    finally:
        provider.shutdown()

    roots = [span for span in spans if span.parent is None]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "harness.run"
    assert root.context is not None
    assert result.trace_id == f"{root.context.trace_id:032x}"
    assert root.attributes["a13n.run.outcome"] == "completed"
    assert root.attributes["a13n.observation.name"] == f"observation-{scenario}"
    assert root.attributes["a13n.user.id"] == "observation-user-42"

    span_ids = {span.context.span_id for span in spans}
    for span in spans:
        assert span.context.trace_id == root.context.trace_id
        assert span.parent is None or span.parent.span_id in span_ids
        assert span.status.status_code != StatusCode.ERROR
        assert span.attributes["langfuse.trace.name"] == f"observation-{scenario}"
        thread_id = span.attributes.get("a13n.thread.id")
        expected_session = (
            "observation-demo-2026-08"
            if thread_id is None or thread_id == root.attributes["a13n.thread.id"]
            else thread_id
        )
        assert span.attributes["langfuse.session.id"] == expected_session
        assert "must-not-be-projected" not in str(dict(span.attributes))

    parent = root
    for name in nested_path:
        children = [
            span
            for span in spans
            if span.parent is not None
            and span.parent.span_id == parent.context.span_id
            and (span.attributes.get("a13n.operation.kind") or span.name) == name
        ]
        assert len(children) == 1, name
        parent = children[0]

    harness_runs = [span for span in spans if span.name == "harness.run"]
    assert len(harness_runs) == (2 if scenario == "subagent" else 1)
    if scenario == "subagent":
        child = next(span for span in harness_runs if span.parent is not None)
        assert child.attributes["a13n.agent.parent_instance.id"] == root.attributes["a13n.agent.instance.id"]
        assert child.attributes["a13n.agent.instance.id"] != root.attributes["a13n.agent.instance.id"]
        assert child.attributes["a13n.delegation.id"]

    generations = [span for span in spans if span.attributes.get("gen_ai.operation.name") == "chat"]
    assert len(generations) == (3 if scenario in {"view", "subagent"} else 2)
    for span in generations:
        attributes = span.attributes
        assert attributes["gen_ai.usage.output_tokens"] > 0
        if attributes["gen_ai.request.model"] == "observation-vision-model":
            assert attributes["gen_ai.usage.input_tokens"] == 64
            assert attributes["gen_ai.usage.cache_read.input_tokens"] == 16
            assert attributes["gen_ai.usage.details.reasoning_tokens"] == 4
            assert attributes["gen_ai.usage.details.input_audio_tokens"] == 2
            assert attributes["gen_ai.usage.details.output_audio_tokens"] == 3
        else:
            assert attributes["gen_ai.usage.input_tokens"] == 120
            assert attributes["gen_ai.usage.cache_creation.input_tokens"] == 12
            assert attributes["gen_ai.usage.cache_read.input_tokens"] == 24
            assert attributes["gen_ai.usage.details.reasoning_tokens"] == 3
            assert attributes["gen_ai.usage.cost"] == pytest.approx(0.00125)
            assert attributes["a13n.usage.pricing.status"] == "applied"
            assert attributes["a13n.usage.cost.source"] == "custom"
            assert attributes["langfuse.observation.metadata.usage_cost_source"] == "custom"
            assert attributes["langfuse.observation.metadata.usage_pricing_rule_id"] == "synthetic-fixed-cost"
