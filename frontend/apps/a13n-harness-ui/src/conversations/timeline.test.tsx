// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { Schema } from "../transport/client";
import {
  ConversationTranscript,
  LiveOutput,
  RecoveryNotice,
} from "./transcript";
import { previewInput, type LocalInput } from "./local-input";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const local = (id = "input-one", text = "Hello"): LocalInput => ({
  id,
  action: "send",
  state: "pending",
  parts: previewInput(id, [text]),
});
const saved = (parts: Schema<"TranscriptPart">[], position = 0) =>
  ({ position, message_kind: "request", parts }) as Schema<"TranscriptEntry">;

it.each(["send", "steer"] as const)(
  "keeps %s input quiet and stable through preparation, partial SSE, acknowledgement and saved cutover",
  (action) => {
    const input: LocalInput = { ...local(), action, state: "preparing" };
    const view = render(
      <ConversationTranscript
        entries={[]}
        blocks={[]}
        localInputs={[input]}
        threadId="one"
      />,
    );
    const message = screen.getByText("Hello");
    expect(screen.queryByRole("status")).toBeNull();
    input.state = "pending";
    const block = {
      id: "run:input:0",
      kind: "user" as const,
      text: "H",
      metadata: input.parts[0].metadata!,
    };
    view.rerender(
      <ConversationTranscript
        entries={[]}
        blocks={[block]}
        localInputs={[input]}
        threadId="one"
      />,
    );
    expect(screen.getByText("Hello")).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText("H")).toBeNull();
    input.state = "accepted";
    view.rerender(
      <ConversationTranscript
        entries={[]}
        blocks={[{ ...block, text: "Hello" }]}
        localInputs={[input]}
        threadId="one"
      />,
    );
    expect(screen.getByText("Hello")).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    view.rerender(
      <ConversationTranscript
        entries={[saved(input.parts as Schema<"TranscriptPart">[])]}
        blocks={[block]}
        localInputs={[]}
        continuation="C1"
        threadId="one"
      />,
    );
    expect(screen.getByText("Hello")).toBe(message);
    expect(screen.getAllByText("Hello")).toHaveLength(1);
    expect(screen.queryByRole("status")).toBeNull();
  },
);

it("does not merge identical submissions with distinct source identities", () => {
  render(
    <ConversationTranscript
      entries={[]}
      blocks={[]}
      localInputs={[local("first"), local("second")]}
      threadId="one"
    />,
  );
  expect(screen.getAllByText("Hello")).toHaveLength(2);
});

it("retains rejected and uncertain input as explicit observations, not successful messages", () => {
  const rejected = {
    ...local("rejected", "Try again"),
    state: "rejected" as const,
  };
  const unknown = {
    ...local("unknown", "Inspect first"),
    state: "unknown" as const,
  };
  render(
    <ConversationTranscript
      entries={[]}
      blocks={[]}
      localInputs={[rejected, unknown]}
      threadId="one"
    />,
  );
  expect(screen.getByText("Not sent · input retained")).toBeTruthy();
  expect(
    screen.getByText("Outcome unknown · review before sending again"),
  ).toBeTruthy();
});

it("preserves unchanged saved nodes across continuations but replaces changed history at the same position", () => {
  const entry = saved([{ kind: "thinking", text: "First plan" }]);
  const view = render(
    <ConversationTranscript
      entries={[entry]}
      blocks={[]}
      localInputs={[]}
      continuation="C1"
      threadId="one"
    />,
  );
  const disclosure = screen.getByText("Reasoning").closest("details")!;
  act(() => {
    disclosure.open = false;
    fireEvent(disclosure, new Event("toggle"));
  });
  view.rerender(
    <ConversationTranscript
      entries={[{ ...entry }]}
      blocks={[]}
      localInputs={[]}
      continuation="C2"
      threadId="one"
    />,
  );
  expect(screen.getByText("Reasoning").closest("details")).toBe(disclosure);
  view.rerender(
    <ConversationTranscript
      entries={[saved([{ kind: "thinking", text: "Replaced history" }])]}
      blocks={[]}
      localInputs={[]}
      continuation="C3"
      threadId="one"
    />,
  );
  expect(screen.getByText("Reasoning").closest("details")).not.toBe(disclosure);
});

it("groups adjacent reasoning across saved entries and the live boundary, but not across prose", () => {
  const entries = [
    saved([{ kind: "thinking", text: "First plan" }]),
    saved([{ kind: "thinking", text: "Second plan" }], 1),
  ];
  render(
    <ConversationTranscript
      entries={entries}
      blocks={[
        { id: "third", kind: "thinking", text: "Third plan" },
        { id: "reply", kind: "assistant", text: "An update" },
        { id: "fourth", kind: "thinking", text: "Fourth plan" },
      ]}
      localInputs={[]}
      threadId="one"
    />,
  );
  expect(screen.getAllByText("Reasoning")).toHaveLength(2);
  const group = screen.getByText("First plan").closest("details");
  expect(screen.getByText("Third plan").closest("details")).toBe(group);
  expect(screen.getByText("Fourth plan").closest("details")).not.toBe(group);
});

it("keeps separate reasoning Markdown documents and stops grouping at tool activity", () => {
  render(
    <LiveOutput
      gap={false}
      blocks={[
        { id: "first", kind: "thinking", text: "```text\nUnfinished fence" },
        { id: "second", kind: "thinking", text: "**A new part**" },
        { id: "tool", kind: "tool", name: "read", text: "{}" },
        { id: "third", kind: "thinking", text: "After the tool" },
      ]}
    />,
  );
  expect(screen.getAllByText("Reasoning")).toHaveLength(2);
  expect(screen.getByText("A new part").tagName).toBe("STRONG");
});

it("retains recovery outcomes and counts subsequent attempts without flashing away", () => {
  vi.useFakeTimers();
  const view = render(
    <RecoveryNotice
      recovery={{ id: "retry-1", state: "retrying", retries: 1 }}
    />,
  );
  act(() => vi.advanceTimersByTime(10000));
  expect(screen.getByRole("status").textContent).toContain("Reconnecting");
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "retry-1", state: "resumed", retries: 1 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("restored");
  act(() => vi.advanceTimersByTime(10000));
  expect(screen.getByRole("status").textContent).toContain(
    "restored · 1 retry",
  );
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "retry-2", state: "retrying", retries: 2 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("2 retries");
});

it("renders owned summaries as distinct collapsed Markdown activity, never heading-matches user input", () => {
  render(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      entries={[
        saved([
          {
            kind: "user",
            text: "**Handoff body**",
            metadata: { "a13n.context": "handoff" },
          },
          {
            kind: "assistant",
            text: "**Compact body**",
            metadata: { "a13n.context": "compaction" },
          },
          { kind: "user", text: "# Context Summary\n\nAuthored input" },
        ]),
      ]}
      blocks={[]}
    />,
  );
  for (const [title, text] of [
    ["Summary", "Handoff body"],
    ["Compact Summary", "Compact body"],
  ]) {
    const group = screen.getByText(title).closest("details")!;
    expect(group.open).toBe(false);
    expect(screen.getByText(text).tagName).toBe("STRONG");
    expect(screen.getByText(text).closest("details")).toBe(group);
  }
  expect(screen.getByText("Authored input").closest("details")).toBeNull();
  expect(screen.getAllByText("User")).toHaveLength(1);
});
