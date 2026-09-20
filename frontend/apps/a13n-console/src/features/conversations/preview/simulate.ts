import type { Schema } from "../../../shared/api";
import {
  ANSWER_REPLY,
  CHECKS_REPLY,
  INCIDENT_REPLY,
  MARKER_COMMAND,
  MARKER_REPLY,
  NOTES_REPLY,
  RESEARCH_REPLY,
  RICH_REPLY,
  STREAM_PATCH,
  TRIAGE_REPLY,
  approvalPresentation,
  questionPresentation,
} from "./content";
import { RunLog, type LogContext } from "./events";

export type ScriptName =
  | "review"
  | "checks"
  | "notes"
  | "marker"
  | "question"
  | "fail"
  | "incident"
  | "research"
  | "approved"
  | "answered";

export interface Simulation {
  log: RunLog;
  status: Schema["RunStatus"];
  outputText: string | null;
  failure: Record<string, unknown> | null;
  pending: Schema["PendingActionResource"][];
  waitReason: string | null;
}

/** Keyword routing: what the agent does follows from what was asked. */
export function chooseScript(prompt: string): ScriptName {
  const lower = prompt.toLowerCase();
  if (/marker|shell|release|approve/.test(lower)) return "marker";
  if (/check|test|fix/.test(lower)) return "checks";
  if (/ask|which|direction|choose/.test(lower)) return "question";
  if (/fail|error|break/.test(lower)) return "fail";
  if (/note|summar|changelog/.test(lower)) return "notes";
  return "review";
}

type Ids = (prefix: string) => string;

export function simulate(
  script: ScriptName,
  context: LogContext,
  id: Ids,
): Simulation {
  const log = new RunLog(context);
  log.accepted();
  log.leased();
  log.running();
  log.advance(120);
  switch (script) {
    case "checks":
      return checks(log, id);
    case "notes":
      return notes(log, id);
    case "marker":
      return marker(log, id);
    case "question":
      return question(log, id);
    case "fail":
      return failing(log, id);
    case "incident":
      return incident(log, id);
    case "research":
      return research(log, id);
    case "approved":
      return approved(log, id);
    case "answered":
      return answered(log, id);
    default:
      return review(log, id);
  }
}

function completed(log: RunLog, outputText: string): Simulation {
  log.completed();
  return {
    log,
    status: "completed",
    outputText,
    failure: null,
    pending: [],
    waitReason: null,
  };
}

function review(log: RunLog, id: Ids): Simulation {
  log.modelRequest(900, { input: 1800, output: 420 }, 2_100);
  log.message(id("msg"), RICH_REPLY, { chunkMs: 28 });
  return completed(log, RICH_REPLY);
}

function research(log: RunLog, id: Ids): Simulation {
  log.modelRequest(700, { input: 1400, output: 90 }, 1_500);
  log.tool(
    id("call"),
    "search_incidents",
    { query: "cursor fold last frame" },
    {
      durationMs: 600,
      result: "INC-118 · closed · stream cursor fold drops terminal frame",
    },
  );
  log.modelRequest(600, { input: 2200, output: 160, cacheRead: 1200 });
  log.message(id("msg"), RESEARCH_REPLY, { chunkMs: 26 });
  return completed(log, RESEARCH_REPLY);
}

/** What the parent did with an asynchronous child's returned result. */
function incident(log: RunLog, id: Ids): Simulation {
  log.modelRequest(600, { input: 2600, output: 130, cacheRead: 2000 }, 2_800);
  log.message(id("msg"), INCIDENT_REPLY, { chunkMs: 24 });
  return completed(log, INCIDENT_REPLY);
}

function checks(log: RunLog, id: Ids): Simulation {
  const recall = `memory-recall-${id("op")}`;
  log.custom("a13n.harness.context", "context", {
    type: "memory_recall_started",
    operation_id: recall,
    scopes: ["thread", "agent"],
  });
  log.advance(220);
  log.custom("a13n.harness.context", "context", {
    type: "memory_recall_completed",
    operation_id: recall,
    scopes: ["thread", "agent"],
    result_count: 3,
  });
  log.modelRequest(900, { input: 3100, output: 210 }, 3_400);
  log.message(
    id("msg"),
    "The suite failed on stream.spec.ts last time. The fold skips the last frame when a batch ends on a boundary. Read the fold, patch applyFrame, then re-run.",
    { kind: "reasoning_message", chunkMs: 18 },
  );
  log.tool(
    id("call"),
    "read_file",
    { path: STREAM_PATCH.filePath },
    {
      durationMs: 150,
      result: "212 lines",
    },
  );
  const edit = id("call");
  log.tool(
    edit,
    "edit_file",
    { path: STREAM_PATCH.filePath, hunks: 1 },
    {
      durationMs: 260,
      result: "Applied 1 hunk",
      before: () =>
        log.capability("a13n.filesystem.edit_applied", {
          file_path: STREAM_PATCH.filePath,
          before: STREAM_PATCH.before,
          after: STREAM_PATCH.after,
        }),
    },
  );

  log.modelRequest(700, { input: 3600, output: 80, cacheRead: 2800 });
  const delegation = `delegation-${id("dlg")}`;
  log.tool(
    id("call"),
    "delegate",
    {
      subagent: "Researcher",
      prompt: "Find prior incidents for cursor folds",
    },
    {
      durationMs: 900,
      result: { execution_id: delegation, subagent_name: "Researcher" },
    },
  );

  log.modelRequest(700, { input: 4200, output: 60, cacheRead: 3500 });
  log.tool(
    id("call"),
    "shell_exec",
    { command: "npm test", cwd: "/workspace" },
    {
      durationMs: 1400,
      result: "42 passed, 0 failed · 2.4s",
    },
  );
  log.modelRequest(600, { input: 5100, output: 190, cacheRead: 4100 });
  log.message(id("msg"), CHECKS_REPLY, { chunkMs: 24 });
  return completed(log, CHECKS_REPLY);
}

function notes(log: RunLog, id: Ids): Simulation {
  const compaction = `compaction-${id("op")}`;
  log.custom("a13n.harness.context", "context", {
    type: "compaction_started",
    operation_id: compaction,
  });
  log.advance(400);
  log.custom("a13n.harness.context", "context", {
    type: "compaction_completed",
    operation_id: compaction,
    removed_messages: 18,
    retained_messages: 6,
  });
  log.modelRequest(800, { input: 2600, output: 120, cacheRead: 1800 }, 2_900);
  const invocation = `delegation-${id("dlg")}`;
  const call = id("call");
  log.tool(
    call,
    "delegate",
    { subagent: "Changelog Writer", prompt: "Draft 2.4.0 release notes" },
    {
      durationMs: 1100,
      result: "Drafted three bullet points for 2.4.0.",
      before: () =>
        log.custom("a13n.harness.delegation", "delegation", {
          type: "inline_delegation",
          invocation_id: invocation,
          action: "completed",
          status: "completed",
          subagent: "Changelog Writer",
          child_instance_id: `${invocation}:child`,
          parent_run_id: log.runId,
          parent_agent_instance_id: `${log.runId}:root`,
          parent_tool_call_id: call,
          child_run_id: `${log.runId}-child`,
        }),
    },
  );
  log.modelRequest(700, { input: 3200, output: 260, cacheRead: 2400 });
  log.message(id("msg"), NOTES_REPLY, { chunkMs: 24 });
  return completed(log, NOTES_REPLY);
}

function marker(log: RunLog, id: Ids): Simulation {
  log.modelRequest(800, { input: 2400, output: 60 }, 2_600);
  const call = id("call");
  log.tool(
    call,
    "shell_exec",
    { command: MARKER_COMMAND, cwd: "/workspace" },
    { pending: true },
  );
  log.advance(150);
  log.custom("a13n.harness.lifecycle", "lifecycle", {
    type: "run_result",
    status: "suspended",
    suspend_reason: "deferred_tool_calls",
    deferred: {
      calls: [],
      approvals: [
        { tool_call_id: call, tool_name: "shell_exec", risk: "high" },
      ],
    },
  });
  log.waiting("approval");
  return {
    log,
    status: "waiting",
    outputText: null,
    failure: null,
    waitReason: "approval",
    pending: [
      {
        call_id: call,
        kind: "approval",
        tool_name: "shell_exec",
        provider_type: null,
        presentation: approvalPresentation(MARKER_COMMAND),
      },
    ],
  };
}

function question(log: RunLog, id: Ids): Simulation {
  log.modelRequest(800, { input: 2100, output: 190 }, 2_300);
  log.message(id("msg"), TRIAGE_REPLY, { chunkMs: 24 });
  const call = id("call");
  log.tool(call, "ask_user_question", { questions: 1 }, { pending: true });
  log.advance(120);
  log.custom("a13n.harness.lifecycle", "lifecycle", {
    type: "run_result",
    status: "suspended",
    suspend_reason: "deferred_tool_calls",
    deferred: {
      approvals: [],
      calls: [{ tool_call_id: call, tool_name: "ask_user_question" }],
    },
  });
  log.waiting("user_input");
  return {
    log,
    status: "waiting",
    outputText: null,
    failure: null,
    waitReason: "user_input",
    pending: [
      {
        call_id: call,
        kind: "user_input",
        tool_name: "ask_user_question",
        provider_type: null,
        presentation: questionPresentation(),
      },
    ],
  };
}

/** The resumption of an approved deferred call. */
function approved(log: RunLog, id: Ids): Simulation {
  log.modelRequest(500, { input: 2700, output: 40, cacheRead: 2100 });
  log.tool(
    id("call"),
    "shell_exec",
    { command: MARKER_COMMAND },
    {
      durationMs: 500,
      result: "exit 0",
    },
  );
  log.modelRequest(500, { input: 3000, output: 90, cacheRead: 2400 });
  log.message(id("msg"), MARKER_REPLY, { chunkMs: 24 });
  return completed(log, MARKER_REPLY);
}

/** The resumption of an answered question. */
function answered(log: RunLog, id: Ids): Simulation {
  log.modelRequest(500, { input: 2400, output: 70, cacheRead: 1800 });
  log.message(id("msg"), ANSWER_REPLY, { chunkMs: 24 });
  return completed(log, ANSWER_REPLY);
}

function failing(log: RunLog, id: Ids): Simulation {
  const attemptFailure = {
    code: "model_rate_limited",
    message: "The model provider rejected the request.",
    details: { provider: "preview", status: 429 },
  };
  log.modelRequestFailed(700, "model_rate_limited");
  log.advance(150);
  log.attemptFailed(attemptFailure);
  log.advance(250);
  log.attempt(id("att"), id("hrn"));
  log.leased();
  log.advance(120);
  log.modelRequestFailed(700, "model_rate_limited");
  log.advance(150);
  const failure = {
    code: "model_rate_limited",
    message: "Attempt 2 exhausted the retry budget.",
    details: { provider: "preview", status: 429 },
    retry_hint: "new_run",
  };
  log.failed(failure);
  return {
    log,
    status: "failed",
    outputText: null,
    failure,
    pending: [],
    waitReason: null,
  };
}
