import type { ContentPart } from "@ag-ui/core";
import { readContentParts } from "a13n-ui";
import { toolExecutionReceipt } from "a13n-ui/display";
import type { Schema } from "../transport/client";
import { ProcessObservations } from "./process-observations";

export type AppliedEdit = { file_path: string; before: string; after: string };
export type ToolView = {
  id: string;
  toolCallId?: string;
  subagentRunId?: string;
  name: string;
  input?: unknown;
  result?: unknown;
  resultParts?: ContentPart[];
  inputComplete?: boolean;
  inputOmitted?: boolean;
  resultOmitted?: boolean;
  outcome?: Schema<"TranscriptPart">["outcome"];
  failure?: string;
  retry?: boolean;
  stopped?: boolean;
  edit?: AppliedEdit;
  images?: Schema<"ToolImageView">[];
  imageUnavailable?: boolean;
  editOmitted?: boolean;
  editPath?: string;
  provider?: string;
  shellCommand?: string;
};
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function parsed(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
export function sourceText(value: unknown): string {
  return typeof value === "string"
    ? value
    : (JSON.stringify(value, null, 2) ?? "");
}
const text = (value: unknown) => (typeof value === "string" ? value : "");

const executionTools = new Set([
  "tool_execution_info",
  "tool_execution_result",
  "tool_execution_wait",
  "tool_execution_cancel",
]);

/** These are observations at return, never a live execution subscription. */
function executionPhase(name: string, output: unknown): string | undefined {
  if (name === "tool_execution_info" && Array.isArray(output))
    return `${output.length} execution${output.length === 1 ? "" : "s"} listed`;
  if (!record(output)) return;
  if (output.status === "unavailable") return "Unavailable";
  if (
    typeof output.execution_id !== "string" ||
    typeof output.tool_name !== "string" ||
    typeof output.run_id !== "string"
  )
    return;
  const status = text(output.status);
  if (
    !["queued", "running", "succeeded", "failed", "cancelled", "lost"].includes(
      status,
    )
  )
    return;
  if (
    name === "tool_execution_cancel" &&
    ["queued", "running"].includes(status)
  )
    return "Cancellation requested";
  return `${status[0].toUpperCase()}${status.slice(1)} at last check`;
}

export function describeTool(tool: ToolView) {
  const input = parsed(tool.input);
  const args = record(input) ? input : {};
  const output = parsed(tool.result);
  const result = record(output) ? output : {};
  const executionReceipt = toolExecutionReceipt(output);
  const path =
    tool.edit?.file_path ||
    tool.editPath ||
    text(result.file_path) ||
    text(args.file_path);
  const shell =
    tool.name.startsWith("shell_") ||
    (!!tool.shellCommand &&
      ["tool_execution_result", "tool_execution_wait"].includes(tool.name));
  const command = shell ? text(args.command) || tool.shellCommand || "" : "";
  const status = record(result.status) ? result.status : {};
  const error = record(result.error) ? result.error : {};
  const failed =
    tool.outcome === "failed" ||
    tool.outcome === "denied" ||
    !!tool.failure ||
    result.ok === false ||
    // OpenAI native search returns its own status with the default success outcome.
    (tool.provider === "openai" &&
      tool.name === "web_search" &&
      result.status === "failed") ||
    (shell &&
      ((typeof status.exit_code === "number" && status.exit_code !== 0) ||
        ["failed", "signaled", "timed_out", "cancelled"].includes(
          text(status.phase),
        )));
  const hasResult =
    tool.result !== undefined ||
    tool.resultParts !== undefined ||
    tool.resultOmitted;
  let phase = tool.retry
    ? "Retry requested"
    : tool.outcome === "interrupted"
      ? "Interrupted"
      : failed
        ? tool.outcome === "denied"
          ? "Denied"
          : "Failed"
        : hasResult
          ? "Result received"
          : tool.stopped
            ? "No result recorded"
            : tool.inputComplete
              ? "Awaiting result"
              : "Receiving input";
  let label = tool.name || "Tool";
  let summary = path;
  let kind:
    "file" | "edit" | "shell" | "search" | "web" | "thread" | "code" | "tool" =
    "tool";
  switch (tool.name) {
    case "computer_observe":
      label = "Observe desktop";
      summary = text(result.target_id) || text(args.alias);
      break;
    case "computer_zoom":
      label = "Inspect desktop region";
      summary = text(args.observation_id);
      break;
    case "computer_describe":
      label = "Inspect desktop";
      summary = text(args.alias);
      break;
    case "computer_click":
    case "computer_move":
    case "computer_drag":
    case "computer_scroll":
    case "computer_type_text":
    case "computer_press_keys":
      label = `Desktop · ${tool.name.slice(9).replaceAll("_", " ")}`;
      summary = text(args.observation_id) || text(args.alias);
      break;
    case "view":
      label = "Read";
      kind = "file";
      break;
    case "write":
      label = args.mode === "a" ? "Append" : "Write";
      kind = "edit";
      break;
    case "edit":
    case "multi_edit":
      label = "Edit";
      kind = "edit";
      break;
    case "ls":
      label = "List";
      kind = "search";
      summary = text(args.path) || ".";
      break;
    case "glob":
      label = "Find files";
      kind = "search";
      summary = text(args.pattern);
      break;
    case "grep":
      label = "Search";
      kind = "search";
      summary = text(args.pattern);
      break;
    case "tool_execution_info":
      label = text(args.execution_id)
        ? "Inspect tool execution"
        : "List tool executions";
      summary = text(args.execution_id);
      break;
    case "tool_execution_result":
      label = "Retrieve tool result";
      summary = text(args.execution_id);
      break;
    case "tool_execution_wait":
      label = "Wait for tool execution";
      summary = text(args.execution_id);
      break;
    case "tool_execution_cancel":
      label = "Cancel tool execution";
      summary = text(args.execution_id);
      break;
    case "shell_exec":
      label = "Run";
      kind = "shell";
      summary = text(args.command);
      break;
    case "shell_wait":
      label = "Read process output";
      kind = "shell";
      summary = text(args.process_id);
      break;
    case "shell_info":
      label =
        record(input) && !text(args.process_id)
          ? "List processes"
          : "Inspect process";
      kind = "shell";
      summary = text(args.process_id) || text(args.alias);
      break;
    case "shell_input":
      label = "Send process input";
      kind = "shell";
      summary = text(args.process_id);
      break;
    case "shell_signal":
      label = "Signal process";
      kind = "shell";
      summary = [text(args.signal), text(args.process_id)]
        .filter(Boolean)
        .join(" · ");
      break;
    case "web_search":
    case "websearch":
    case "search":
    case "search_query":
      label =
        args.type === "open_page"
          ? "Read webpage"
          : args.type === "find_in_page"
            ? "Find on webpage"
            : "Search the web";
      kind = "web";
      summary =
        text(args.query) || text(args.q) || text(args.prompt) || text(args.url);
      break;
    case "fetch":
    case "scrape":
    case "crawl":
      label = "Read webpage";
      kind = "web";
      summary = text(args.url);
      break;
    case "download":
      label = "Download";
      kind = "web";
      summary = Array.isArray(args.urls)
        ? args.urls
            .filter((url): url is string => typeof url === "string")
            .join(", ")
        : text(args.url);
      break;
    case "list_threads":
      label = "Find conversations";
      kind = "thread";
      summary = text(args.query) || text(args.project_id) || "All projects";
      break;
    case "get_thread":
      label = "Inspect conversation";
      kind = "thread";
      summary = text(args.thread_id) || "Current conversation";
      break;
    case "list_projects":
    case "list_agents":
    case "list_models":
      label = `Find ${tool.name.slice(5)}`;
      kind = "search";
      summary = text(args.query) || "Configured resources";
      break;
    case "get_project":
      label = "Inspect project";
      kind = "search";
      summary = text(args.project_id);
      break;
    case "create_thread":
      label = "Start conversation";
      kind = "thread";
      summary = text(args.title) || text(args.prompt);
      break;
    case "run_thread":
      label = "Continue conversation";
      kind = "thread";
      summary = text(args.prompt);
      break;
    case "steer_thread":
      label = "Send instruction";
      kind = "thread";
      summary = text(args.message);
      break;
    case "send_thread_message":
      label = "Message conversation";
      kind = "thread";
      summary = text(args.message);
      break;
    case "run_program":
      label = "Run program";
      kind = "code";
      summary = text(args.path);
      break;
    case "run_code":
      label = "Run code";
      kind = "code";
      break;
    case "task_create":
      label = "Create task";
      summary = text(args.subject);
      break;
    case "task_update":
      label = "Update task";
      summary = [text(args.task_id), text(args.status).replaceAll("_", " ")]
        .filter(Boolean)
        .join(" · ");
      break;
    case "task_get":
    case "task_list":
      label = tool.name === "task_get" ? "Inspect task" : "List tasks";
      summary = text(args.task_id);
      break;
    case "note_write":
    case "note_delete":
    case "note_get":
      label =
        tool.name === "note_write"
          ? "Update note"
          : tool.name === "note_delete"
            ? "Delete note"
            : "Read notes";
      summary = text(args.key);
      break;
    case "delegate":
      label = "Delegate";
      summary = text(args.subagent_name);
      break;
    case "subagent_info":
    case "wait_subagent":
    case "steer_subagent":
    case "cancel_subagent":
    case "resume_subagent":
      label = {
        subagent_info: "Inspect subagent",
        wait_subagent: "Wait for subagents",
        steer_subagent: "Guide subagent",
        cancel_subagent: "Stop subagent",
        resume_subagent: "Resume subagent",
      }[tool.name];
      summary = text(args.execution_id);
      break;
    case "call":
      if (
        typeof args.group === "string" &&
        typeof args.tool === "string" &&
        record(args.arguments)
      ) {
        summary = `${args.group} / ${args.tool}`;
      }
      break;
  }
  if (shell && command && tool.name !== "shell_exec")
    summary = [summary, command].filter(Boolean).join(" · ");
  // An unfinished or omitted argument object cannot establish default targets.
  if (!record(input) && !hasResult && !tool.edit && !tool.editOmitted)
    summary = "";
  if (tool.edit || tool.editOmitted) {
    label = "Edit";
    kind = "edit";
  }
  if (
    hasResult &&
    !tool.retry &&
    !tool.failure &&
    !["failed", "denied", "interrupted"].includes(tool.outcome ?? "")
  ) {
    if (executionReceipt) phase = "Execution handed off";
    else if (result.accepted === false) phase = "Not accepted";
    else if (
      !failed &&
      record(result.receipt) &&
      ["create_thread", "run_thread"].includes(tool.name)
    )
      phase = "Run accepted";
    else if (
      !failed &&
      result.ok === true &&
      ["run", "steer"].includes(text(result.mode)) &&
      tool.name === "send_thread_message"
    )
      phase =
        result.mode === "run"
          ? "Message accepted · new turn"
          : "Message accepted · instruction";
    else if (
      !failed &&
      tool.name === "steer_thread" &&
      result.accepted === true
    )
      phase = "Instruction accepted";
  }
  if (executionTools.has(tool.name)) {
    if (phase === "Awaiting result" && tool.name === "tool_execution_wait")
      phase = "Waiting";
    else if (phase === "Result received" && !tool.resultOmitted)
      phase = executionPhase(tool.name, output) ?? phase;
  }
  if (
    shell &&
    phase === "Result received" &&
    typeof status.phase === "string" &&
    status.phase &&
    (status.phase === "running" ||
      !["shell_exec", "shell_start"].includes(tool.name))
  )
    phase = `${status.phase[0].toUpperCase()}${status.phase.slice(1).replaceAll("_", " ")} at last check`;
  const detail = record(result.thread) ? result.thread : {};
  const thread = record(detail.thread) ? detail.thread : detail;
  const receipt = record(result.receipt) ? result.receipt : {};
  const threadId =
    kind === "thread" && tool.name !== "list_threads"
      ? text(result.thread_id) ||
        text(thread.thread_id) ||
        text(receipt.thread_id) ||
        text(args.thread_id)
      : "";
  const threadTitle =
    text(thread.title) ||
    (tool.name === "create_thread" ? text(args.title) : "");
  const errorText = tool.failure || text(error.message) || text(error.code);
  const stdout = record(result.stdout) ? result.stdout : {};
  const stderr = record(result.stderr) ? result.stderr : {};
  const outputIncomplete = [stdout, stderr].some(
    (stream) =>
      stream.content_complete === false ||
      stream.coverage === "partial" ||
      (typeof stream.omitted_before_bytes === "number" &&
        stream.omitted_before_bytes > 0),
  );
  return {
    args,
    result,
    path,
    label,
    summary,
    kind,
    phase,
    failed,
    errorText,
    outputIncomplete,
    threadId,
    threadTitle,
    executionReceipt,
    shell,
    command,
  };
}

export function questionReceipt(tool: ToolView) {
  if (tool.name !== "ask_user_question" || tool.resultOmitted) return;
  const info = describeTool(tool);
  if (info.phase !== "Result received") return;
  const { answers, response } = info.result;
  if (!record(answers)) return;
  const questions = Array.isArray(info.args.questions)
    ? info.args.questions.filter(record)
    : [];
  const items: {
    title: string;
    question: string;
    values: { label: string; description?: string }[];
  }[] = [];
  for (const [question, answer] of Object.entries(answers)) {
    const values = typeof answer === "string" ? [answer] : answer;
    if (
      !Array.isArray(values) ||
      !values.length ||
      !values.every(
        (value): value is string => typeof value === "string" && !!value.trim(),
      )
    )
      return;
    const original = questions.find((item) => item.question === question);
    const options = Array.isArray(original?.options)
      ? original.options.filter(record)
      : [];
    items.push({
      title: text(original?.header) || question,
      question,
      values: values.map((label) => ({
        label,
        description: text(
          options.find((option) => option.label === label)?.description,
        ),
      })),
    });
  }
  const order = (question: string) => {
    const index = questions.findIndex((item) => item.question === question);
    return index < 0 ? questions.length : index;
  };
  items.sort((left, right) => order(left.question) - order(right.question));
  if (typeof response === "string" && response.trim())
    items.push({
      title: "Response",
      question: "",
      values: [{ label: response }],
    });
  if (!items.length) return;
  return { items, questions };
}

/** Pair only loaded, visible calls with their following result; page-edge results remain visible. */
export function savedTools(entries: Schema<"TranscriptEntry">[]) {
  type Part = Schema<"TranscriptPart">;
  const views = new Map<Part, ToolView | null>();
  const pending = new Map<string, Part>();
  const identity = (part: Part) =>
    `${part.provider ?? "function"}:${part.tool_call_id}`;
  for (const entry of entries)
    for (const [index, part] of entry.parts.entries()) {
      if (part.metadata?.display === false) continue;
      if (part.kind === "tool_call") {
        views.set(part, {
          id: `${entry.position}:${index}`,
          toolCallId: part.tool_call_id ?? undefined,
          name: part.tool_name || "Tool",
          input: part.value ?? part.text ?? undefined,
          provider: part.provider ?? undefined,
          inputOmitted: part.value_omitted,
          inputComplete: true,
          stopped: true,
        });
        if (part.tool_call_id) pending.set(identity(part), part);
      } else if (
        part.kind === "tool_result" ||
        (part.kind === "retry" && part.tool_call_id)
      ) {
        const call = part.tool_call_id
          ? pending.get(identity(part))
          : undefined;
        const view = call ? views.get(call) : undefined;
        const completed: ToolView = {
          ...(view || {
            id: `${entry.position}:${index}`,
            name: part.tool_name || "Tool",
          }),
          toolCallId: part.tool_call_id ?? undefined,
          result: part.kind === "retry" ? part.text : part.value,
          resultParts: part.content_parts?.length
            ? readContentParts(part.content_parts)
            : undefined,
          resultOmitted: part.value_omitted,
          outcome: part.outcome ?? undefined,
          provider: part.provider ?? view?.provider,
          edit:
            part.applied_edit?.before != null &&
            part.applied_edit?.after != null
              ? {
                  file_path: part.applied_edit.file_path,
                  before: part.applied_edit.before,
                  after: part.applied_edit.after,
                }
              : undefined,
          images: part.tool_images,
          imageUnavailable: part.tool_image_unavailable,
          editOmitted: part.applied_edit?.omitted,
          editPath: part.applied_edit?.file_path,
          retry: part.kind === "retry",
          ...(part.kind === "retry"
            ? { failure: part.text || "Tool input validation failed." }
            : {}),
          inputComplete: true,
          stopped: true,
        };
        if (call && view) {
          views.set(call, completed);
          views.set(part, null);
          pending.delete(identity(part));
        } else views.set(part, completed);
      }
    }
  // Saved history has no Run scope for native process handles. Only correlate
  // Host execution IDs backed by a visible receipt, never process-* handles.
  const processes = new ProcessObservations();
  for (const [part, tool] of views) {
    if (!tool || tool.provider) continue;
    const receipt = toolExecutionReceipt(parsed(tool.result));
    if (receipt?.tool_name === "shell_exec")
      processes.result("saved", tool.name, tool.input, tool.result);
    const shellCommand = processes.command("saved", tool.name, tool.input);
    if (shellCommand) views.set(part, { ...tool, shellCommand });
  }
  return views;
}

/** Only offer a same-spelling Host lookup; never rebase an Environment path. */
export function hostLookupPath(path: string): string | undefined {
  if (!path || /[\0\r\n]/.test(path) || /^\/environment(?:\/|$)/.test(path))
    return;
  if (
    path.startsWith("/") ||
    /^[A-Za-z]:[\\/]/.test(path) ||
    /^\\\\[^\\]+\\[^\\]+/.test(path)
  )
    return path;
}

export const MAX_ACTIVITY_TOOLS = 16;
export type ActivityKind =
  "explore" | "shell" | "web" | "edit" | "work" | "subagents" | "executions";
export function activityKind(tool: ToolView): ActivityKind | undefined {
  if (executionTools.has(tool.name)) return "executions";
  if (
    [
      "task_create",
      "task_update",
      "task_get",
      "task_list",
      "note_write",
      "note_delete",
      "note_get",
    ].includes(tool.name)
  )
    return "work";
  if (
    [
      "delegate",
      "subagent_info",
      "wait_subagent",
      "steer_subagent",
      "cancel_subagent",
      "resume_subagent",
    ].includes(tool.name)
  )
    return "subagents";
  const kind = describeTool(tool).kind;
  if (kind === "edit" || kind === "shell" || kind === "web") return kind;
  if (kind === "file" || kind === "search") return "explore";
}

/** Group adjacent operations only; messages and other visible content remain boundaries. */
export function savedToolGroups(entries: Schema<"TranscriptEntry">[]) {
  const views = savedTools(entries);
  const groups = new Map<Schema<"TranscriptPart">, ToolView[] | null>();
  let current: ToolView[] | undefined;
  let kind: ActivityKind | undefined;
  for (const entry of entries) {
    for (const part of entry.parts) {
      if (part.metadata?.display === false) continue;
      const tool = views.get(part);
      if (tool === null) {
        groups.set(part, null);
        continue;
      }
      const nextKind = tool && activityKind(tool);
      if (
        tool &&
        current &&
        current.length < MAX_ACTIVITY_TOOLS &&
        nextKind &&
        nextKind === kind
      ) {
        current.push(tool);
        groups.set(part, null);
      } else {
        current = tool ? [tool] : undefined;
        kind = nextKind;
        if (current) groups.set(part, current);
      }
    }
  }
  return groups;
}

/** Summarize the latest observation per handle without rewriting earlier receipts. */
function shellGroupStatus(
  tools: ToolView[],
  infos: ReturnType<typeof describeTool>[],
) {
  const latest = new Map<string, string>();
  tools.forEach((tool, index) => {
    const info = infos[index];
    const status = record(info.result.status) ? info.result.status : {};
    if (!info.executionReceipt && !text(status.phase)) return;
    const reference =
      text(info.result.execution_id) ||
      text(info.args.process_id) ||
      text(info.args.execution_id) ||
      text(info.result.process_id);
    const key = reference
      ? JSON.stringify([tool.subagentRunId ?? "", reference])
      : `unlinked:${index}`;
    latest.set(key, info.phase);
  });
  const phases = [...latest.values()];
  if (phases.includes("Execution handed off")) return "Execution handed off";
  if (phases.includes("Running at last check")) return "Running at last check";
  return "";
}

export function activitySummary(tools: ToolView[]) {
  const infos = tools.map(describeTool);
  const kind = activityKind(tools[0]);
  const shellStatus = kind === "shell" ? shellGroupStatus(tools, infos) : "";
  const count = (n: number, noun: string) =>
    `${n} ${noun}${n === 1 ? "" : "s"}`;
  const files = new Set(infos.map((info) => info.path).filter(Boolean)).size;
  const pendingTools = tools.filter(
    (tool) =>
      tool.result === undefined &&
      tool.resultParts === undefined &&
      !tool.resultOmitted &&
      !tool.stopped &&
      !tool.failure &&
      !tool.retry,
  );
  const pending = pendingTools.length;
  const issues = infos.filter(
    (info) =>
      info.failed ||
      [
        "Interrupted",
        "Retry requested",
        "No result recorded",
        "Not accepted",
        "Unavailable",
        "Failed at last check",
        "Lost at last check",
      ].includes(info.phase),
  );
  let title: string;
  if (kind === "executions")
    title = `Tool executions · ${count(tools.length, "operation")}`;
  else if (kind === "work" || kind === "subagents")
    title = `${kind === "work" ? "Tasks & notes" : "Subagents"} · ${count(tools.length, "operation")}`;
  else if (kind === "edit")
    title = `File changes · ${files ? count(files, "file") : count(tools.length, "operation")}`;
  else if (kind === "shell") {
    const commands = tools.filter((tool) => tool.name === "shell_exec").length;
    title = `${pending ? "Running" : "Ran"} ${commands ? count(commands, "command") : count(tools.length, "process operation")}`;
    if (!pending && (issues.length || shellStatus))
      title = `Shell · ${count(tools.length, "operation")}`;
  } else if (kind === "web")
    title = `${pending ? "Browsing" : "Browsed"} the web · ${count(tools.length, "action")}`;
  else {
    const reads = tools.filter((tool) => tool.name === "view").length;
    const searches = tools.length - reads;
    title = `${pending ? "Exploring" : "Explored"} ${[reads && count(files || reads, "file"), searches && count(searches, "lookup")].filter(Boolean).join(" · ")}`;
  }
  const notices = issues.filter(
    (info) => info.phase !== "Failed" || kind === "executions",
  );
  const waiting = pendingTools.filter(
    (tool) => tool.name === "tool_execution_wait",
  ).length;
  const cancellations = tools.filter(
    (tool) => tool.name === "tool_execution_cancel",
  ).length;
  const lastPhase = infos.at(-1)?.phase;
  return {
    title,
    issue: issues.length > 0,
    status: [
      !pending && !issues.length ? shellStatus : "",
      waiting ? `${waiting} waiting` : "",
      pending > waiting ? `${pending - waiting} running` : "",
      kind === "executions" && cancellations
        ? count(cancellations, "cancellation request")
        : "",
      kind === "executions" &&
      !pending &&
      !issues.length &&
      lastPhase !== "Result received" &&
      lastPhase !== "Cancellation requested"
        ? lastPhase
        : "",
      notices.length === 1
        ? notices[0].phase
        : notices.length
          ? `${notices.length} need attention`
          : "",
    ]
      .filter(Boolean)
      .join(" · "),
  };
}
