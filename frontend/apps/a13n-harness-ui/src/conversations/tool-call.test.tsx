// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { ToolCall, OpenHostFile, editPatch } from "./tool-call";
import {
  describeTool,
  hostLookupPath,
  savedTools,
  savedToolGroups,
} from "./tool-presentation";
import { LiveOutput, SavedEntry } from "./transcript";
import type { Schema } from "../transport/client";
afterEach(cleanup);

it("pairs loaded calls with results once, retaining page-edge results and repeated call IDs", () => {
  const entries = [
    {
      position: 0,
      parts: [
        { kind: "tool_result", tool_call_id: "orphan", value: "edge" },
        {
          kind: "tool_call",
          tool_call_id: "same",
          tool_name: "edit",
          value: { file_path: "/tmp/a", old_string: "old", new_string: "new" },
        },
      ],
    },
    {
      position: 1,
      parts: [
        {
          kind: "tool_result",
          tool_call_id: "same",
          outcome: "denied",
          value: "No permission",
        },
        { kind: "tool_call", tool_call_id: "same", tool_name: "view" },
        {
          kind: "tool_call",
          tool_call_id: "hidden",
          metadata: { display: false },
        },
      ],
    },
    {
      position: 2,
      parts: [
        { kind: "tool_result", tool_call_id: "same", value: { content: "ok" } },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  const tools = savedTools(entries);
  expect(tools.get(entries[0].parts[0])?.result).toBe("edge");
  expect(tools.get(entries[0].parts[1])?.outcome).toBe("denied");
  expect(tools.get(entries[1].parts[0])).toBeNull();
  expect(tools.get(entries[1].parts[1])?.result).toEqual({ content: "ok" });
  expect(tools.has(entries[1].parts[2])).toBe(false);
  render(
    <>
      {entries.map((entry) => (
        <SavedEntry
          key={entry.position}
          entry={entry}
          toolGroups={savedToolGroups(entries)}
        />
      ))}
    </>,
  );
  expect(screen.getAllByText("Denied")).toHaveLength(1);
  expect(document.querySelectorAll("[data-tool-id]")).toHaveLength(3);
});

it("keeps input completion distinct from execution success and handles failure envelopes", () => {
  const tool = { id: "a", name: "shell_exec", input: '{"command":' };
  expect(describeTool(tool).phase).toBe("Receiving input");
  expect(describeTool({ ...tool, inputComplete: true }).phase).toBe(
    "Awaiting result",
  );
  expect(
    describeTool({ ...tool, inputComplete: true, stopped: true }).phase,
  ).toBe("No result recorded");
  expect(
    describeTool({ ...tool, result: { ok: true, status: { exit_code: 2 } } })
      .phase,
  ).toBe("Failed");
  expect(
    describeTool({
      ...tool,
      result: { ok: false, error: { message: "Not found" } },
    }).errorText,
  ).toBe("Not found");
  expect(
    describeTool({ id: "a", name: "write", input: { mode: "a" } }).label,
  ).toBe("Append");
});

it("shows actual applied content without confusing source +++ lines or missing final newlines", () => {
  const edit = { file_path: "/tmp/a", before: "++old", after: "++new" };
  const lines = editPatch(edit)?.hunks[0].lines;
  expect(lines).toContain("-++old");
  expect(lines).toContain("+++new");
  expect(lines).toContain("\\ No newline at end of file");
  expect(
    editPatch({ ...edit, before: "x".repeat(512 * 1024 + 1) }),
  ).toBeUndefined();
  render(
    <ToolCall
      tool={{
        id: "a",
        name: "edit",
        edit,
        result: "late failure",
        outcome: "failed",
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  expect(screen.getByText("Applied edit")).toBeTruthy();
  expect(screen.getByText("+1")).toBeTruthy();
  expect(screen.queryByText("Requested replacement")).toBeNull();
});

it("labels historical replacement as input and offers explicit host lookup outside the disclosure", () => {
  const open = vi.fn();
  render(
    <OpenHostFile value={open}>
      <ToolCall
        tool={{
          id: "a",
          name: "edit",
          input: { file_path: "/tmp/a", old_string: "old", new_string: "new" },
          result: { ok: true },
        }}
      />
    </OpenHostFile>,
  );
  const link = screen.getByRole("button", { name: "Open on host" });
  expect(link.parentElement?.closest("button")).toBeNull();
  fireEvent.click(link);
  expect(open).toHaveBeenCalledWith("/tmp/a");
  expect(screen.queryByText("Requested replacement")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  expect(screen.getByText("Requested replacement")).toBeTruthy();
  expect(screen.queryByText("Applied edit")).toBeNull();
  for (const path of [
    "a.py",
    "workspace/a.py",
    "workspace:/a.py",
    "/environment/a.py",
    "\n/tmp/a",
  ])
    expect(hostLookupPath(path)).toBeUndefined();
  expect(hostLookupPath("C:\\work\\a.py")).toBe("C:\\work\\a.py");
});

it("renders tool calls inline and exposes bounded shell output with raw fallback", () => {
  const { container } = render(
    <LiveOutput
      gap={false}
      blocks={[
        { id: "one", kind: "assistant", text: "Before" },
        {
          id: "two",
          kind: "tool",
          name: "shell_exec",
          text: '{"command":"printf hello"}',
          result: JSON.stringify({
            status: { phase: "exited", exit_code: 0 },
            stdout: { text: "hello", content_complete: false },
            stderr: { text: "warning" },
          }),
          done: true,
        },
        { id: "three", kind: "assistant", text: "After" },
      ]}
    />,
  );
  const html = container.innerHTML;
  expect(html.indexOf("Before")).toBeLessThan(
    html.indexOf('data-tool-id="two"'),
  );
  expect(html.indexOf('data-tool-id="two"')).toBeLessThan(
    html.indexOf("After"),
  );
  fireEvent.click(screen.getByRole("button", { name: /Ran 1 command/ }));
  expect(screen.getByText("hello")).toBeTruthy();
  expect(screen.getByText("warning")).toBeTruthy();
  expect(screen.getByText(/Partial process output/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Arguments & result" }));
  expect(screen.getByRole("button", { name: "Raw" })).toBeTruthy();
});

it("preserves retry requests as a distinct saved tool status", () => {
  const entries = [
    {
      position: 0,
      parts: [
        { kind: "tool_call", tool_call_id: "a", tool_name: "edit", value: {} },
      ],
    },
    {
      position: 1,
      parts: [
        { kind: "retry", tool_call_id: "a", text: "Choose a unique match" },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  const tool = savedTools(entries).get(entries[0].parts[0])!;
  expect(describeTool(tool).phase).toBe("Retry requested");
  render(<ToolCall tool={tool} />);
  fireEvent.click(screen.getByRole("button", { name: /Retry requested/ }));
  expect(screen.getByText("Choose a unique match")).toBeTruthy();
});

it("does not present interrupted history repair as a received execution result", () => {
  const tool = {
    id: "interrupted",
    name: "edit",
    result: "Run interrupted",
    outcome: "interrupted" as const,
  };
  expect(describeTool(tool).phase).toBe("Interrupted");
  render(<ToolCall tool={tool} />);
  expect(screen.getByText("Interrupted", { exact: true })).toBeTruthy();
  expect(screen.queryByText("Result received")).toBeNull();
});
