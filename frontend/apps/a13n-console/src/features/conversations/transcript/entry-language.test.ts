import type { TFunction } from "i18next";
import { expect, it } from "vitest";
import type { ActionEntry, ContentEntry } from "../timeline";
import {
  entryLabel,
  entrySubject,
  asksAQuestion,
  workState,
} from "./entry-language";

/** The interpolating translator both disclosure levels are tested with. */
const t = ((key: string, options?: Record<string, unknown>) =>
  options
    ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
    : key) as unknown as TFunction;

function action(fields: Partial<ActionEntry> = {}): ActionEntry {
  return {
    kind: "tool",
    id: "step",
    name: null,
    arguments: null,
    result: null,
    failure: null,
    edit: null,
    providerUsage: null,
    waitingReason: null,
    hitl: null,
    dispatchOnly: false,
    childExecutionId: null,
    callCount: null,
    outcome: null,
    startedAt: null,
    endedAt: null,
    durationMs: null,
    state: "completed",
    children: [],
    ...fields,
  };
}

it("keeps tool names in monospace and everything else in prose", () => {
  expect(entryLabel(action({ name: "read_file" }), t)).toEqual({
    name: "read_file",
    mono: true,
  });
  // A step that reported no wire name is named by what it was, not in code.
  expect(entryLabel(action(), t)).toEqual({ name: "Tool call", mono: false });
  expect(
    entryLabel(action({ kind: "subagent", name: "Researcher" }), t),
  ).toEqual({ name: "Delegated to Researcher", mono: false });
  expect(entryLabel(action({ kind: "compaction" }), t).name).toBe(
    "Context compaction",
  );
  expect(entryLabel(action({ kind: "memory" }), t).name).toBe("Memory recall");
  expect(entryLabel(action({ kind: "handoff" }), t).name).toBe(
    "Context handoff",
  );
  expect(entryLabel(action({ kind: "hitl", hitl: "approval" }), t).name).toBe(
    "Approval",
  );
});

it("reads the subject from the call, the question or the result", () => {
  expect(entrySubject(action({ arguments: { path: "src/stream.ts" } }))).toBe(
    "src/stream.ts",
  );
  expect(entrySubject(action({ result: "42 passed" }))).toBe("42 passed");
  const asking = action({
    kind: "hitl",
    hitl: "question",
    arguments: {
      questions: [
        {
          header: "Direction",
          question: "Which reply should I send?",
          options: [
            { label: "A", description: "first" },
            { label: "B", description: "second" },
          ],
        },
      ],
    },
  });
  expect(entrySubject(asking)).toBe("Which reply should I send?");
  // A delegation is about the task it sent, not about the subagent it names.
  expect(
    entrySubject(
      action({
        kind: "subagent",
        name: "reviewer",
        arguments: {
          subagent: "reviewer",
          prompt: "Check the migration for rollout safety",
        },
      }),
    ),
  ).toBe("Check the migration for rollout safety");
  expect(asksAQuestion(asking)).toBe(true);
  expect(asksAQuestion(action({ hitl: "approval" }))).toBe(false);
});

it("shows the reasoning excerpt at both levels", () => {
  const reasoning: ContentEntry = {
    kind: "reasoning",
    id: "thought",
    text: "Checking the fold before editing",
    steeringSource: null,
    protectedReasoning: false,
    failure: null,
    startedAt: null,
    endedAt: null,
    durationMs: null,
    state: "completed",
  };
  expect(entryLabel(reasoning, t).name).toBe("Reasoning");
  expect(entrySubject(reasoning)).toBe("Checking the fold before editing");
});

it("reads an unfinished step against the run that owns it", () => {
  expect(workState("completed", "running")).toBe("done");
  expect(workState("in_progress", "running")).toBe("working");
  // A sealed waiting Run keeps its unresolved call open, not spinning.
  expect(workState("in_progress", "waiting")).toBe("waiting");
  expect(workState("interrupted", "waiting")).toBe("waiting");
  // A Run that stopped never leaves a call working.
  expect(workState("in_progress", "cancelled")).toBe("interrupted");
  expect(workState("rejected", "completed")).toBe("failed");
  // Nothing observable: the row says nothing rather than claiming success.
  expect(workState("observed", "completed")).toBe("unknown");
});
