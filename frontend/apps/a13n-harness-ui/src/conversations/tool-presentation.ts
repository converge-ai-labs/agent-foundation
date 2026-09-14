import type { Schema } from "../transport/client";

export type AppliedEdit = { file_path: string; before: string; after: string };
export type ToolView = {
  id: string;
  name: string;
  input?: unknown;
  result?: unknown;
  inputComplete?: boolean;
  inputOmitted?: boolean;
  resultOmitted?: boolean;
  outcome?: Schema<"TranscriptPart">["outcome"];
  failure?: string;
  retry?: boolean;
  stopped?: boolean;
  edit?: AppliedEdit;
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

export function describeTool(tool: ToolView) {
  const input = parsed(tool.input);
  const args = record(input) ? input : {};
  const output = parsed(tool.result);
  const result = record(output) ? output : {};
  const path =
    tool.edit?.file_path || text(result.file_path) || text(args.file_path);
  const status = record(result.status) ? result.status : {};
  const error = record(result.error) ? result.error : {};
  const failed =
    tool.outcome === "failed" ||
    tool.outcome === "denied" ||
    !!tool.failure ||
    result.ok === false ||
    (tool.name.startsWith("shell") &&
      ((typeof status.exit_code === "number" && status.exit_code !== 0) ||
        ["failed", "signaled", "timed_out", "cancelled"].includes(
          text(status.phase),
        )));
  const hasResult = tool.result !== undefined || tool.resultOmitted;
  const phase = tool.retry
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
  let kind: "file" | "edit" | "shell" | "search" | "code" | "tool" = "tool";
  switch (tool.name) {
    case "view":
      label = "Read";
      kind = "file";
      break;
    case "write":
      label = args.mode === "a" ? "Append" : "Write";
      kind = "file";
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
      label = "Inspect process";
      kind = "shell";
      summary = text(args.process_id);
      break;
    case "shell_input":
      label = "Send process input";
      kind = "shell";
      summary = text(args.process_id);
      break;
    case "run_code":
      label = "Run code";
      kind = "code";
      break;
    case "delegate":
      label = "Delegate";
      summary = text(args.subagent_name);
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
  if (tool.edit) {
    label = "Edit";
    kind = "edit";
  }
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
  };
}

/** Pair only loaded, visible calls with their following result; page-edge results remain visible. */
export function savedTools(entries: Schema<"TranscriptEntry">[]) {
  type Part = Schema<"TranscriptPart">;
  const views = new Map<Part, ToolView | null>();
  const pending = new Map<string, Part>();
  for (const entry of entries)
    for (const [index, part] of entry.parts.entries()) {
      if (part.metadata?.display === false) continue;
      if (part.kind === "tool_call") {
        views.set(part, {
          id: `${entry.position}:${index}`,
          name: part.tool_name || "Tool",
          input: part.value ?? part.text ?? undefined,
          inputOmitted: part.value_omitted,
          inputComplete: true,
          stopped: true,
        });
        if (part.tool_call_id) pending.set(part.tool_call_id, part);
      } else if (
        part.kind === "tool_result" ||
        (part.kind === "retry" && part.tool_call_id)
      ) {
        const call = part.tool_call_id
          ? pending.get(part.tool_call_id)
          : undefined;
        const view = call ? views.get(call) : undefined;
        const completed: ToolView = {
          ...(view || {
            id: `${entry.position}:${index}`,
            name: part.tool_name || "Tool",
          }),
          result: part.kind === "retry" ? part.text : part.value,
          resultOmitted: part.value_omitted,
          outcome: part.outcome ?? undefined,
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
          pending.delete(part.tool_call_id!);
        } else views.set(part, completed);
      }
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
