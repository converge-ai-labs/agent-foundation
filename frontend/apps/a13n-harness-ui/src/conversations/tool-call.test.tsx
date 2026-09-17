// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  within,
} from "@testing-library/react";
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

it.each(["", "\n"])(
  "retains every replacement line and final-newline metadata beyond the minimal diff budget (%j)",
  (ending) => {
    const before = Array.from({ length: 2001 }, (_, i) => `before ${i}`);
    const after = Array.from({ length: 2001 }, (_, i) => `after ${i}`);
    const patch = editPatch({
      file_path: "/tmp/large",
      before: before.join("\n") + ending,
      after: after.join("\n") + ending,
    });
    const marker = ending ? [] : ["\\ No newline at end of file"];
    expect(patch.hunks).toEqual([
      {
        oldStart: 1,
        oldLines: 2001,
        newStart: 1,
        newLines: 2001,
        lines: [
          ...before.map((line) => `-${line}`),
          ...marker,
          ...after.map((line) => `+${line}`),
          ...marker,
        ],
      },
    ]);
  },
);

it("shows actual applied content without confusing source +++ lines or missing final newlines", () => {
  const edit = { file_path: "/tmp/a", before: "++old", after: "++new" };
  const lines = editPatch(edit)?.hunks[0].lines;
  expect(lines).toContain("-++old");
  expect(lines).toContain("+++new");
  expect(lines).toContain("\\ No newline at end of file");
  expect(
    editPatch({ ...edit, before: "x".repeat(512 * 1024 + 1) }).hunks[0].lines,
  ).toContain(`-${"x".repeat(512 * 1024 + 1)}`);
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
  expect(
    within(screen.getByRole("region", { name: "stdout" })).getByText("hello"),
  ).toBeTruthy();
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

it("renders multi-edit requested fragments as diffs without claiming applied evidence", () => {
  render(
    <ToolCall
      tool={{
        id: "multi",
        name: "multi_edit",
        input: {
          file_path: "/tmp/test.py",
          edits: [
            { old_string: "before-one", new_string: "after-one" },
            { old_string: "before-two", new_string: "after-two" },
          ],
        },
        result: { ok: true },
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  expect(screen.getAllByText("Requested replacement")).toHaveLength(2);
  expect(screen.queryByText("Applied edit")).toBeNull();
  expect(screen.getByText("+after-one")).toBeTruthy();
  expect(screen.getByText("-before-two")).toBeTruthy();
});

it("shows the whole recorded written file rather than a diff or a requested fragment", () => {
  const after = 'const value = "saved";\n' + "// more\n".repeat(2000);
  render(
    <ToolCall
      tool={{
        id: "write",
        name: "write",
        input: { file_path: "/tmp/test.ts", content: "not the recorded file" },
        edit: { file_path: "/tmp/test.ts", before: "old", after },
        result: { ok: true },
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  const region = screen.getByRole("region", { name: "Written content" });
  expect(region.querySelector("code")?.textContent).toBe(after);
  expect(region.querySelector(".hljs-keyword")).not.toBeNull();
  expect(screen.queryByText("Applied edit")).toBeNull();
});

const questionTool = {
  id: "question",
  name: "ask_user_question",
  input: {
    questions: [
      {
        header: "Scope",
        question: "Which scope?",
        options: [
          { label: "WebUI", description: "Only the browser surface" },
          { label: "All", description: "Every surface" },
        ],
      },
      {
        header: "Checks",
        question: "Which checks?",
        multiSelect: true,
        options: [
          { label: "Tests", description: "Run tests" },
          { label: "Browser", description: "Check rendering" },
        ],
      },
    ],
  },
  result: {
    answers: { "Which scope?": "WebUI", "Which checks?": ["Tests", "Browser"] },
  },
};

it("shows question context and selected option descriptions by default, keeping all options in the disclosure", () => {
  render(
    <ToolCall
      tool={{
        ...questionTool,
        result: {
          answers: {
            "Which checks?": ["Tests", "Browser"],
            "Which scope?": "WebUI",
          },
        },
      }}
    />,
  );
  expect(
    Array.from(document.querySelectorAll("dt"), (item) => item.textContent),
  ).toEqual(["ScopeWhich scope?", "ChecksWhich checks?"]);
  const receipt = screen.getByRole("region", { name: "Your answers" });
  for (const text of [
    "Scope",
    "Which scope?",
    "WebUI",
    "Only the browser surface",
    "Checks",
    "Which checks?",
    "Tests",
    "Run tests",
    "Browser",
    "Check rendering",
  ])
    expect(within(receipt).getByText(text)).toBeTruthy();
  expect(screen.queryByText("Every surface")).toBeNull();
  const toggle = screen.getByRole("button", { name: "Questions & details" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(screen.getAllByText("Which scope?")).toHaveLength(2);
  expect(screen.getAllByText("Only the browser surface")).toHaveLength(2);
  expect(screen.getByText("Every surface")).toBeTruthy();
  fireEvent.click(toggle);
  expect(screen.getByText("Which scope?")).toBeTruthy();
  expect(screen.getByText("Only the browser surface")).toBeTruthy();
  expect(screen.getByText("WebUI")).toBeTruthy();
});

it("preserves free-text and general responses in live and saved question receipts", () => {
  const result = {
    answers: { "Which scope?": "A custom scope\nwith a second line" },
    response: "Please keep the rest unchanged.",
  };
  const { unmount } = render(
    <LiveOutput
      gap={false}
      blocks={[
        {
          id: "question",
          kind: "tool",
          name: questionTool.name,
          text: JSON.stringify(questionTool.input),
          result: JSON.stringify(result),
          done: true,
        },
      ]}
    />,
  );
  expect(screen.getByText(/A custom scope/).textContent).toBe(
    result.answers["Which scope?"],
  );
  expect(screen.getByText("Please keep the rest unchanged.")).toBeTruthy();
  expect(screen.getByText("Which scope?")).toBeTruthy();
  expect(screen.queryByText("Only the browser surface")).toBeNull();
  unmount();
  const entries = [
    {
      position: 0,
      parts: [
        {
          kind: "tool_call",
          tool_call_id: "q",
          tool_name: questionTool.name,
          value: questionTool.input,
        },
      ],
    },
    {
      position: 1,
      parts: [
        {
          kind: "tool_result",
          tool_call_id: "q",
          value: result,
          outcome: "success",
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
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
  expect(screen.getAllByRole("region", { name: "Your answers" })).toHaveLength(
    1,
  );
  expect(screen.getByText("Scope")).toBeTruthy();
  expect(screen.getByText("Which scope?")).toBeTruthy();
  expect(screen.queryByText("Only the browser surface")).toBeNull();
  expect(screen.getByText(/A custom scope/).textContent).toBe(
    result.answers["Which scope?"],
  );
});

it("keeps questions and answers readable without original headers or matching options", () => {
  render(
    <ToolCall
      tool={{
        ...questionTool,
        input: {
          questions: [
            {
              question: "Which scope?",
              options: [{ label: "WebUI" }, null],
            },
          ],
        },
        result: {
          answers: {
            "Which scope?": "WebUI",
            "Another question?": "Custom answer",
          },
        },
      }}
    />,
  );
  expect(screen.getAllByText("Which scope?")).toHaveLength(1);
  expect(screen.getByText("WebUI")).toBeTruthy();
  expect(screen.getAllByText("Another question?")).toHaveLength(1);
  expect(screen.getByText("Custom answer")).toBeTruthy();
});

it("keeps missing, omitted, malformed and unsuccessful question results in the ordinary tool view", () => {
  for (const changes of [
    { result: undefined },
    { result: { answers: {} } },
    { result: { answers: { "Which scope?": [42] } } },
    { resultOmitted: true },
    { outcome: "failed" as const },
    { outcome: "denied" as const },
    { outcome: "interrupted" as const },
    { retry: true },
  ]) {
    const { unmount } = render(
      <ToolCall tool={{ ...questionTool, ...changes }} />,
    );
    expect(screen.queryByRole("region", { name: "Your answers" })).toBeNull();
    expect(
      screen.getByRole("button", { name: /ask_user_question/ }),
    ).toBeTruthy();
    unmount();
  }
});
