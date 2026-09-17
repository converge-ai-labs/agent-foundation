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
import {
  conversationTitle,
  previewInput,
  type LocalInput,
} from "./local-input";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const local = (id = "input-one", text = "Hello"): LocalInput => ({
  id,
  action: "send",
  state: "accepted",
  parts: previewInput(id, [text]),
});
const saved = (parts: Schema<"TranscriptPart">[], position = 0) =>
  ({ position, message_kind: "request", parts }) as Schema<"TranscriptEntry">;

it.each(["send", "steer"] as const)(
  "waits for server evidence before showing %s input and keeps accepted/saved cutover stable",
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
    expect(screen.queryByText("Hello")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    input.state = "pending";
    view.rerender(
      <ConversationTranscript
        entries={[]}
        blocks={[]}
        localInputs={[input]}
        threadId="one"
      />,
    );
    expect(screen.queryByText("Hello")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
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
    const message = screen.getByText("Hello");
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
        turns={
          action === "send"
            ? [
                {
                  turn_id: input.id,
                  input_position: 0,
                  end_position: 1,
                  preview: "Hello",
                },
              ]
            : []
        }
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

it("keeps rejected and uncertain local input out of the transcript", () => {
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
  expect(screen.queryByText("Try again")).toBeNull();
  expect(screen.queryByText("Inspect first")).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

it.each(["preparing", "pending", "rejected", "unknown", "accepted"] as const)(
  "uses only acknowledged local input for bubbles and title fallback (%s)",
  (state) => {
    const input = { ...local(), state };
    render(
      <ConversationTranscript
        entries={[]}
        blocks={[]}
        localInputs={[input]}
        threadId="one"
      />,
    );
    expect(!!screen.queryByText("Hello")).toBe(state === "accepted");
    expect(conversationTitle(undefined, [input])).toBe(
      state === "accepted" ? "Hello" : "Untitled conversation",
    );
  },
);

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
    expect(group.querySelector("summary")!.textContent).toContain(text);
    expect(group.querySelector("strong")!.textContent).toBe(text);
    expect(group.querySelector("summary")!.textContent).toContain("Saved");
  }
  expect(screen.getByText("Authored input").closest("details")).toBeNull();
  expect(screen.getAllByText("User")).toHaveLength(1);
});
it("keeps history and an expanded summary through saved cutover and later continuations", () => {
  const text =
    "# Context Summary\n\n**Keep decisions**\n\n- Verify the result\n\n```ts\nconst done = true;\n```";
  const prior = saved([{ kind: "assistant", text: "Earlier answer" }]);
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={[prior]}
      localInputs={[]}
      blocks={[
        {
          id: "context:handoff-one",
          kind: "activity",
          context: "handoff",
          name: "Summary",
          text: "Completed",
          result: text,
        },
      ]}
    />,
  );
  const earlier = screen.getByText("Earlier answer");
  const details = screen.getByText("Summary").closest("details")!;
  expect(details.open).toBe(false);
  expect(details.querySelector("summary")!.textContent).toContain(
    "Keep decisions",
  );
  expect(details.querySelector("summary")!.textContent).not.toContain(
    "Context Summary",
  );
  details.open = true;
  const summary = saved(
    [
      {
        kind: "user",
        text,
        metadata: { "a13n.context": "handoff", operation_id: "handoff-one" },
      },
    ],
    1,
  );
  view.rerender(
    <ConversationTranscript
      threadId="one"
      continuation="checkpoint-one"
      entries={[prior, summary]}
      localInputs={[]}
      blocks={[]}
    />,
  );
  expect(screen.getByText("Summary").closest("details")).toBe(details);
  expect(details.open).toBe(true);
  expect(screen.getByText("Earlier answer")).toBe(earlier);
  expect(details.querySelector("h1")!.textContent).toBe("Context Summary");
  expect(details.querySelector("strong")!.textContent).toBe("Keep decisions");
  expect(details.querySelector("li")!.textContent).toBe("Verify the result");
  expect(details.querySelector("code")!.textContent).toContain(
    "const done = true;",
  );
  view.rerender(
    <ConversationTranscript
      threadId="one"
      continuation="checkpoint-two"
      entries={[
        prior,
        summary,
        saved([{ kind: "assistant", text: "Continued answer" }], 2),
      ]}
      localInputs={[]}
      blocks={[]}
    />,
  );
  expect(screen.getByText("Summary").closest("details")).toBe(details);
  expect(details.open).toBe(true);
  expect(screen.getByText("Earlier answer")).toBe(earlier);
});

it("collapses the whole completed process including steering, preserving all final text parts", () => {
  const input = local("round", "Original input");
  const entries = [
    saved(input.parts as Schema<"TranscriptPart">[], 0),
    saved(
      [
        { kind: "thinking", text: "Internal plan" },
        { kind: "assistant", text: "Progress update" },
      ],
      1,
    ),
    saved(
      previewInput("steer", ["Change direction"]) as Schema<"TranscriptPart">[],
      2,
    ),
    saved(
      [
        { kind: "thinking", text: "Final reasoning" },
        { kind: "assistant", text: "Final part one" },
        { kind: "assistant", text: "Final part two" },
      ],
      3,
    ),
  ];
  const turn = {
    turn_id: "round",
    input_position: 0,
    end_position: 4,
    final_position: 3,
    preview: "Original input",
    steering_count: 1,
  };
  const view = render(
    <ConversationTranscript
      entries={entries}
      turns={[turn]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  const toggle = screen.getByRole("button", { name: /Execution details/ });
  expect(toggle.textContent).toContain("1 additional input");
  expect(toggle.textContent).not.toContain("steering");
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Change direction").closest("[hidden]")).toBeTruthy();
  expect(screen.getByText("Progress update").closest("[hidden]")).toBeTruthy();
  expect(screen.getByText("Final reasoning").closest("[hidden]")).toBeTruthy();
  for (const text of ["Original input", "Final part one", "Final part two"])
    expect(screen.getByText(text).closest("[hidden]")).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getByText("Change direction").closest("[hidden]")).toBeNull();
  view.rerender(
    <ConversationTranscript
      entries={[...entries]}
      turns={[{ ...turn }]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
      continuation="new-head"
    />,
  );
  expect(
    screen
      .getByRole("button", { name: /Execution details/ })
      .getAttribute("aria-expanded"),
  ).toBe("true");
});

it("keeps unfinished work open and only auto-collapses after a saved final boundary", () => {
  const entries = [
    saved(previewInput("round", ["Input"]) as Schema<"TranscriptPart">[], 0),
    saved([{ kind: "assistant", text: "Working" }], 1),
  ];
  const turn = {
    turn_id: "round",
    input_position: 0,
    end_position: 2,
    preview: "Input",
  };
  const view = render(
    <ConversationTranscript
      entries={entries}
      turns={[turn]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  expect(
    screen
      .getByRole("button", { name: /Execution details/ })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  view.rerender(
    <ConversationTranscript
      entries={[...entries, saved([{ kind: "assistant", text: "Done" }], 2)]}
      turns={[{ ...turn, end_position: 3, final_position: 2 }]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  expect(
    screen
      .getByRole("button", { name: /Execution details/ })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.getByText("Done").closest("[hidden]")).toBeNull();
});

it("keeps boundary input and final visible while earlier process pages load", () => {
  const load = vi.fn();
  render(
    <ConversationTranscript
      entries={[
        saved(
          previewInput("round", ["Original"]) as Schema<"TranscriptPart">[],
          0,
        ),
        saved([{ kind: "assistant", text: "Final" }], 80),
      ]}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 81,
          final_position: 80,
          preview: "Original",
        },
      ]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
      loadEarlier={load}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Execution details" }));
  fireEvent.click(screen.getByRole("button", { name: "Load earlier steps" }));
  expect(load).toHaveBeenCalledOnce();
  expect(screen.getAllByText("Original")).toHaveLength(1);
  expect(screen.getAllByText("Final")).toHaveLength(1);
});

it("keeps legacy closing output outside manually collapsed execution details", () => {
  render(
    <ConversationTranscript
      threadId="one"
      entries={[
        saved(
          previewInput("round", ["Question"]) as Schema<"TranscriptPart">[],
          0,
        ),
        saved([{ kind: "assistant", text: "Progress" }], 1),
        saved([{ kind: "assistant", text: "Saved closing answer" }], 2),
      ]}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 3,
          output_position: 2,
          preview: "Question",
        },
      ]}
      blocks={[]}
      localInputs={[]}
    />,
  );
  const toggle = screen.getByRole("button", { name: "Execution details" });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(toggle);
  expect(screen.getByText("Progress").closest("[hidden]")).toBeTruthy();
  expect(
    screen.getByText("Saved closing answer").closest("[hidden]"),
  ).toBeNull();
});

it("retains the saved final outside details when live rows arrive before history refresh", () => {
  render(
    <ConversationTranscript
      threadId="one"
      entries={[
        saved(
          previewInput("round", ["Question"]) as Schema<"TranscriptPart">[],
          0,
        ),
        saved([{ kind: "assistant", text: "Earlier process" }], 1),
        saved([{ kind: "assistant", text: "Saved final" }], 2),
      ]}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 3,
          final_position: 2,
          preview: "Question",
        },
      ]}
      blocks={[{ id: "live", kind: "assistant", text: "New progress" }]}
      localInputs={[]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Execution details" }));
  expect(screen.getByText("Saved final").closest("[hidden]")).toBeNull();
  expect(screen.getByText("Earlier process").closest("[hidden]")).toBeTruthy();
  expect(screen.getByText("New progress").closest("[hidden]")).toBeNull();
  expect(
    screen
      .getByText("Saved final")
      .compareDocumentPosition(screen.getByText("New progress")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("groups only adjacent notifications in order across saved/live cutover", () => {
  const notice = (text: string, source = "background_process") => ({
    kind: "user" as const,
    text,
    metadata: { "a13n.steering-source": source },
  });
  const parts = [
    notice("Process exited with code 1"),
    notice("Child completed", "async_subagent"),
    { kind: "user" as const, text: "Please investigate the exit" },
    notice("Another process update"),
    { kind: "assistant" as const, text: "Checking now" },
    notice("Another child update", "async_subagent"),
    {
      kind: "tool_call" as const,
      tool_name: "read",
      tool_call_id: "read-1",
      value: {},
    },
    notice("Last process update"),
  ];
  const view = render(
    <ConversationTranscript
      entries={[saved([parts[0]])]}
      blocks={parts.slice(1).map((part, i) => ({
        id: `live-${i}`,
        kind: part.kind === "tool_call" ? "tool" : part.kind,
        text: part.text ?? "{}",
        metadata: "metadata" in part ? part.metadata : undefined,
        name: "tool_name" in part ? part.tool_name : undefined,
      }))}
      localInputs={[]}
      threadId="one"
    />,
  );
  const check = () => {
    const group = screen.getByText("System updates · 2").closest("details")!;
    expect(group.open).toBe(false);
    expect(group.querySelectorAll("details")).toHaveLength(0);
    expect(
      [...group.querySelectorAll("pre")].map((node) => node.textContent),
    ).toEqual(["Process exited with code 1", "Child completed"]);
    expect(
      screen.getByText("Please investigate the exit").closest("details"),
    ).toBeNull();
    expect(
      screen.getByText("Another process update").closest("details"),
    ).not.toBe(group);
    expect(
      screen.getByText("Another child update").closest("details"),
    ).not.toBe(screen.getByText("Last process update").closest("details"));
    expect(
      [...view.container.querySelectorAll("details > summary")].filter((node) =>
        /^(System updates|Process update|Subagent update)/.test(
          node.textContent ?? "",
        ),
      ),
    ).toHaveLength(4);
  };
  check();
  view.rerender(
    <ConversationTranscript
      entries={parts.map((part, i) => saved([part], i))}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  check();
});

it.each([0, 2])(
  "keeps whole-turn counts with unloaded history (%s additional inputs)",
  (count) => {
    render(
      <ConversationTranscript
        entries={[
          saved([{ kind: "user", text: "Question" }]),
          saved([{ kind: "assistant", text: "Answer" }], 80),
        ]}
        turns={[
          {
            turn_id: "round",
            input_position: 0,
            end_position: 81,
            final_position: 80,
            preview: "Question",
            tool_count: 128,
            steering_count: count,
          },
        ]}
        blocks={[]}
        localInputs={[]}
        threadId="one"
      />,
    );
    const toggle = screen.getByRole("button", { name: /Execution details/ });
    expect(toggle.textContent).toBe(
      `Execution details · 128 tool calls${count ? " · 2 additional inputs" : ""}`,
    );
  },
);
