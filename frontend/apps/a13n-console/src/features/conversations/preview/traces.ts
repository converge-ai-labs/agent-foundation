import type { Schema } from "../../../shared/api";
import { ORGANIZATION_ID } from "./factories";
import type { PreviewRun, PreviewTrace } from "./model";

interface Span {
  name: string;
  type: string;
  offsetMs: number;
  durationMs: number;
  input?: unknown;
  output?: unknown;
  model?: string;
  usage?: { input: number; output: number };
  cost?: string;
  status?: "ok" | "error";
  statusMessage?: string;
  children?: Span[];
}

const SCOPE: Schema["InstrumentationScope"] = {
  name: "a13n.service",
  version: "0.0.0",
  attributes: null,
};

/**
 * One trace correlated to a Run, shaped as the Service's own instrumentation:
 * the attempt span carries a Harness run, which carries model calls and tools.
 */
export function traceFor(
  run: PreviewRun,
  workspaceId: string,
  spans: Span[],
): PreviewTrace {
  const attemptId = run.attempts[0]?.id ?? `att_${run.run.id}`;
  const root = describe(
    {
      name: "a13n.service.run_attempt",
      type: "span",
      offsetMs: 0,
      durationMs: totalMs(spans),
      children: [
        {
          name: "harness.run",
          type: "agent",
          offsetMs: 40,
          durationMs: Math.max(totalMs(spans) - 80, 200),
          input: run.run.input_text,
          output: run.run.output_text,
          children: spans,
        },
      ],
    },
    run,
    `obs_${run.run.id}`,
    null,
  );
  return {
    trace: {
      id: `trc_${run.run.id}`,
      provider: "preview",
      source_url: null,
      correlation: {
        agent_id: run.run.agent_id,
        organization_id: ORGANIZATION_ID,
        run_attempt_id: attemptId,
        run_id: run.run.id,
        session_id: run.run.session_id,
        thread_id: run.run.thread_id,
        workspace_id: workspaceId,
      },
      root: root.observation,
    },
    observations: root.flat,
  };
}

function totalMs(spans: readonly Span[]): number {
  return spans.reduce(
    (end, span) => Math.max(end, span.offsetMs + span.durationMs + 80),
    600,
  );
}

function describe(
  span: Span,
  run: PreviewRun,
  id: string,
  parentId: string | null,
): { observation: Schema["Observation"]; flat: Schema["Observation"][] } {
  const start = Date.parse(run.run.created_at);
  const observation: Schema["Observation"] = {
    id,
    name: span.name,
    type: span.type,
    parent_id: parentId,
    started_at: new Date(start + span.offsetMs).toISOString(),
    ended_at: new Date(start + span.offsetMs + span.durationMs).toISOString(),
    status: span.status ?? "ok",
    status_message: span.statusMessage ?? null,
    level: span.status === "error" ? "error" : "info",
    cost_usd: span.cost ?? null,
    model: span.model ? { requested: span.model, response: span.model } : null,
    usage: span.usage
      ? {
          input_tokens: span.usage.input,
          output_tokens: span.usage.output,
          total_tokens: span.usage.input + span.usage.output,
        }
      : null,
    input:
      span.input === undefined
        ? null
        : { media_type: "application/json", value: span.input },
    output:
      span.output === undefined
        ? null
        : { media_type: "application/json", value: span.output },
    attributes: {
      "a13n.run_id": run.run.id,
      "a13n.thread_id": run.run.thread_id,
    },
    resource_attributes: { "service.name": "a13n-service" },
    scope: SCOPE,
    events: null,
    links: null,
  };
  const flat = [observation];
  (span.children ?? []).forEach((child, index) => {
    const nested = describe(child, run, `${id}_${index}`, id);
    flat.push(...nested.flat);
  });
  return { observation, flat };
}

/** The model and tool work behind the checks Run, in the order it happened. */
export function checksSpans(): Span[] {
  return [
    {
      name: "memory.recall",
      type: "retriever",
      offsetMs: 120,
      durationMs: 220,
      output: { results: 3 },
    },
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 360,
      durationMs: 900,
      model: "gpt-5",
      usage: { input: 3100, output: 210 },
      cost: "0.005975",
      input: { messages: 2 },
      output: { parts: ["reasoning", "tool_call"] },
    },
    {
      name: "read_file",
      type: "tool",
      offsetMs: 1300,
      durationMs: 150,
      input: { path: "src/stream.ts" },
      output: "212 lines",
    },
    {
      name: "edit_file",
      type: "tool",
      offsetMs: 1470,
      durationMs: 260,
      input: { path: "src/stream.ts", hunks: 1 },
      output: "Applied 1 hunk",
    },
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 1760,
      durationMs: 700,
      model: "gpt-5",
      usage: { input: 3600, output: 80 },
      cost: "0.005300",
      output: { parts: ["tool_call"] },
    },
    {
      name: "delegate",
      type: "tool",
      offsetMs: 2480,
      durationMs: 900,
      input: { subagent: "Researcher" },
      output: { subagent_name: "Researcher" },
    },
    {
      name: "shell_exec",
      type: "tool",
      offsetMs: 3400,
      durationMs: 1400,
      input: { command: "npm test" },
      output: "42 passed, 0 failed · 2.4s",
    },
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 4830,
      durationMs: 600,
      model: "gpt-5",
      usage: { input: 5100, output: 190 },
      cost: "0.008275",
      output: { parts: ["text"] },
    },
  ];
}

/** A single-call Run: one generation and nothing else. */
export function reviewSpans(): Span[] {
  return [
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 120,
      durationMs: 900,
      model: "gpt-5",
      usage: { input: 1800, output: 420 },
      cost: "0.006450",
      input: { messages: 2 },
      output: { parts: ["text"] },
    },
  ];
}

/** The failed Run: two rejected provider calls under one attempt. */
export function failedSpans(): Span[] {
  return [
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 120,
      durationMs: 700,
      model: "gpt-5",
      status: "error",
      statusMessage: "model_rate_limited",
      output: { error: "rate_limited" },
    },
    {
      name: "gpt-5",
      type: "generation",
      offsetMs: 1240,
      durationMs: 700,
      model: "gpt-5",
      status: "error",
      statusMessage: "model_rate_limited",
      output: { error: "rate_limited" },
    },
  ];
}
