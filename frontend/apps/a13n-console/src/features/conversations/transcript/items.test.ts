import { expect, it } from "vitest";
import type { PresentedItem } from "../projection";
import { retainedTimeline } from "./fixture";
import { transcriptBlocks, workSummary, type TranscriptBlock } from "./items";

function item(
  id: string,
  kind: string,
  fields: Partial<PresentedItem> = {},
): PresentedItem {
  return {
    id,
    kind,
    state: "completed",
    firstPosition: "1-0",
    lastPosition: "1-0",
    startedAt: null,
    endedAt: null,
    text: "",
    role: "assistant",
    toolName: "",
    arguments: "",
    protectedReasoning: false,
    ...fields,
  };
}

const tool = (id: string, name: string, args: unknown, fields = {}) =>
  item(id, "tool_call", {
    toolName: name,
    arguments: JSON.stringify(args),
    ...fields,
  });

/** Chat reads the same timeline Debug renders, built from the same Items. */
const blocksOf = (items: PresentedItem[], runState = "completed") =>
  transcriptBlocks(
    retainedTimeline(
      items.map((entry, index) => ({
        ...entry,
        firstPosition: `${index + 1}-0`,
        lastPosition: `${index + 1}-0`,
      })),
    ).entries,
    runState,
  );

const work = (block: TranscriptBlock) =>
  block.kind === "work" ? block.entries : [];

it("gathers contiguous work between replies and keeps prose separate", () => {
  const blocks = blocksOf([
    tool("tool-1", "search_docs", { query: "cursor" }),
    item("thought", "reasoning_message"),
    item("reply", "text_message", { text: "Final response" }),
    tool("tool-2", "read_file", { path: "src/stream.ts" }),
    item("output", "run_output"),
    item("hidden", "tool_call", { display: false }),
  ]);
  expect(
    blocks.map((block) =>
      block.kind === "work" ? work(block).map((entry) => entry.id) : block.id,
    ),
  ).toEqual([["tool-1", "thought"], "reply", ["tool-2"]]);
});

it("keeps the run's own request out of the transcript and shows later user text as guidance", () => {
  const blocks = blocksOf([
    item("request", "text_message", { role: "user", text: "The request" }),
    tool("tool-1", "read_file", { path: "src/stream.ts" }),
    item("steer", "text_message", {
      role: "user",
      text: "Use the smaller scope",
    }),
    item("reply", "text_message", { text: "Done" }),
  ]);
  expect(blocks.map((block) => [block.kind, block.id])).toEqual([
    ["work", "tool-1"],
    ["guidance", "steer"],
    ["message", "reply"],
  ]);
});

it("treats explicitly sourced steering as guidance even when it leads the run", () => {
  const blocks = blocksOf([
    item("steer", "text_message", {
      role: "user",
      text: "Prefer the smaller scope",
      steeringSource: "harness",
    }),
  ]);
  expect(blocks.map((block) => block.kind)).toEqual(["guidance"]);
});

it("carries the timeline entry itself so both levels read the same step", () => {
  const entries = work(
    blocksOf([
      tool("read", "read_file", { path: "src/stream.ts" }),
      item("thought", "reasoning_message", { text: "Checking the fold" }),
    ])[0]!,
  );
  expect(entries.map((work) => [work.entry.kind, work.entry.id])).toEqual([
    ["tool", "read"],
    ["reasoning", "thought"],
  ]);
});

it("names three steps and counts the rest, reasoning included", () => {
  const summary = workSummary(
    work(
      blocksOf([
        tool("a", "read_file", { path: "a.ts" }),
        item("thought", "reasoning_message"),
        tool("b", "edit_file", { path: "b.ts" }),
        tool("c", "shell_exec", { command: "npm test" }),
        tool("d", "read_file", { path: "d.ts" }),
      ])[0]!,
    ),
  );
  expect(summary.named.map((entry) => entry.id)).toEqual(["a", "b", "c"]);
  expect(summary.more).toBe(2);
});

it("reports the wall time the steps actually reported and nothing when they did not", () => {
  const observed = workSummary(
    work(
      blocksOf([
        tool(
          "a",
          "read_file",
          { path: "a.ts" },
          {
            startedAt: "2026-09-18T00:00:00.000Z",
            endedAt: "2026-09-18T00:00:00.150Z",
          },
        ),
        tool(
          "b",
          "shell_exec",
          { command: "npm test" },
          {
            startedAt: "2026-09-18T00:00:00.200Z",
            endedAt: "2026-09-18T00:00:03.500Z",
          },
        ),
      ])[0]!,
    ),
  );
  expect(observed.durationMs).toBe(3500);
  expect(observed.named[1]?.entry.durationMs).toBe(3300);
  expect(
    workSummary(work(blocksOf([tool("a", "read_file", {})])[0]!)).durationMs,
  ).toBe(null);
});

it("holds an unresolved call open while the run waits for a person", () => {
  const entries = work(
    blocksOf(
      [
        tool("done", "read_file", { path: "a.ts" }),
        tool(
          "open",
          "shell_exec",
          { command: "echo hi" },
          { state: "interrupted" },
        ),
      ],
      "waiting",
    )[0]!,
  );
  const summary = workSummary(entries);
  expect(summary.waiting?.name).toBe("shell_exec");
  expect(summary.running).toBe(false);
  expect(entries.map((entry) => entry.state)).toEqual(["done", "waiting"]);
});

it("does not keep an unresolved call working after the run stopped", () => {
  const items = [
    tool("done", "read_file", { path: "a.ts" }),
    tool(
      "open",
      "shell_exec",
      { command: "npm test" },
      { state: "in_progress" },
    ),
  ];
  expect(
    work(blocksOf(items, "cancelled")[0]!).map((entry) => entry.state),
  ).toEqual(["done", "interrupted"]);
  expect(
    work(blocksOf(items, "running")[0]!).map((entry) => entry.state),
  ).toEqual(["done", "working"]);
});
