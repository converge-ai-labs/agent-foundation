// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema, Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
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

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

it("keeps every input and text part visible between independent execution segments", () => {
  const entries = [
    saved(
      previewInput("round", ["Original input"]) as Schema<"TranscriptPart">[],
      0,
    ),
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
  const transcript = (continuation?: string) => (
    <ConversationTranscript
      entries={[...entries]}
      turns={[{ ...turn }]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
      continuation={continuation}
    />
  );
  const view = render(transcript());
  const toggles = screen.getAllByRole("button", { name: /Execution details/ });
  expect(toggles).toHaveLength(2);
  expect(toggles.map((toggle) => toggle.getAttribute("aria-expanded"))).toEqual(
    ["false", "false"],
  );
  for (const text of [
    "Original input",
    "Progress update",
    "Change direction",
    "Final part one",
    "Final part two",
  ]) {
    expect(
      screen.getByText(text).closest("[data-execution-reader]"),
    ).toBeNull();
    expect(screen.getByText(text).closest("[hidden]")).toBeNull();
  }
  expect(screen.getByText("Internal plan").closest("[hidden]")).toBeTruthy();
  expect(screen.getByText("Final reasoning").closest("[hidden]")).toBeTruthy();
  expect(view.container.textContent).toMatch(
    /Original input.*Internal plan.*Progress update.*Change direction.*Final reasoning.*Final part one.*Final part two/s,
  );
  fireEvent.click(toggles[0]);
  view.rerender(transcript("new-head"));
  expect(
    screen
      .getAllByRole("button", { name: /Execution details/ })
      .map((toggle) => toggle.getAttribute("aria-expanded")),
  ).toEqual(["true", "false"]);
});

it("defaults unfinished details closed without treating prose as successful completion", () => {
  const entries = [
    saved(previewInput("round", ["Input"]) as Schema<"TranscriptPart">[], 0),
    saved(
      [
        { kind: "thinking", text: "Plan" },
        { kind: "assistant", text: "Working" },
      ],
      1,
    ),
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
  const toggle = screen.getByRole("button", { name: /Execution details/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Working").closest("[hidden]")).toBeNull();
  fireEvent.click(toggle);
  view.rerender(
    <ConversationTranscript
      entries={[...entries, saved([{ kind: "assistant", text: "Done" }], 2)]}
      turns={[{ ...turn, end_position: 3, final_position: 2 }]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Done").closest("[hidden]")).toBeNull();
});

it("marks missing conversation history outside details without inventing an execution segment", () => {
  render(
    <ConversationTranscript
      entries={[
        saved([{ kind: "user", text: "Original" }], 0),
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
    />,
  );
  expect(
    screen.queryByRole("button", { name: /Execution details/ }),
  ).toBeNull();
  expect(
    screen.getByText("Turn history is incomplete.").closest("[hidden]"),
  ).toBeNull();
  for (const text of ["Original", "Final"])
    expect(screen.getAllByText(text)).toHaveLength(1);
});

it("keeps legacy output and a resumed live suffix in chronological order", () => {
  const entries = [
    saved([{ kind: "user", text: "Question" }], 0),
    saved(
      [
        { kind: "thinking", text: "Plan" },
        { kind: "assistant", text: "Progress" },
      ],
      1,
    ),
    saved([{ kind: "assistant", text: "Saved closing answer" }], 2),
  ];
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={entries}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 3,
          output_position: 2,
          preview: "Question",
        },
      ]}
      blocks={[{ id: "live", kind: "assistant", text: "New progress" }]}
      localInputs={[]}
    />,
  );
  for (const text of ["Progress", "Saved closing answer", "New progress"])
    expect(screen.getByText(text).closest("[hidden]")).toBeNull();
  expect(view.container.textContent).toMatch(
    /Progress.*Saved closing answer.*New progress/s,
  );
  fireEvent.click(screen.getByRole("button", { name: "Execution details" }));
  expect(screen.getByText("Plan").closest("[hidden]")).toBeNull();
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

it("counts only the tool calls in each execution segment", () => {
  render(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      entries={[saved([{ kind: "user", text: "Question" }], 0)]}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 1,
          preview: "Question",
          tool_count: 128,
          steering_count: 2,
        },
      ]}
      blocks={[
        {
          id: "tool-one",
          kind: "tool",
          name: "shell_exec",
          text: "{}",
          done: true,
        },
        { id: "progress", kind: "assistant", text: "Progress" },
        { id: "tool-two", kind: "tool", name: "view", text: "{}", done: true },
        {
          id: "tool-three",
          kind: "tool",
          name: "view",
          text: "{}",
          done: true,
        },
      ]}
    />,
  );
  expect(
    screen
      .getAllByRole("button", { name: /Execution details/ })
      .map((toggle) => toggle.textContent),
  ).toEqual([
    "Execution details · 1 tool call",
    "Execution details · 2 tool calls",
  ]);
});

it.each([false, true])(
  "keeps trailing saved-history gaps reachable before live output (%s)",
  (live) => {
    const view = render(
      <ConversationTranscript
        threadId="one"
        entries={[saved([{ kind: "user", text: "Prompt" }], 0), saved([], 1)]}
        turns={[
          {
            turn_id: "round",
            input_position: 0,
            end_position: 4,
            preview: "Prompt",
          },
        ]}
        blocks={
          live
            ? [{ id: "progress", kind: "assistant", text: "Live progress" }]
            : []
        }
        localInputs={[]}
      />,
    );
    const gap = screen.getByText("Turn history is incomplete.");
    expect(gap.closest("[hidden]")).toBeNull();
    if (live)
      expect(view.container.textContent).toMatch(/incomplete.*Live progress/s);
  },
);

it("leaves applied live steering and agent text outside folded execution", () => {
  render(
    <ConversationTranscript
      threadId="one"
      entries={[
        saved(
          previewInput("round", ["Prompt"]) as Schema<"TranscriptPart">[],
          0,
        ),
      ]}
      turns={[
        {
          turn_id: "round",
          input_position: 0,
          end_position: 1,
          preview: "Prompt",
        },
      ]}
      blocks={[
        { id: "plan", kind: "thinking", text: "Plan" },
        { id: "progress", kind: "assistant", text: "Checking" },
        {
          id: "steer",
          kind: "user",
          text: "Change focus",
          metadata: { "a13n.input-source": "steer" },
        },
        { id: "next-plan", kind: "thinking", text: "New plan" },
        { id: "reply", kind: "assistant", text: "Changed focus" },
      ]}
      localInputs={[]}
    />,
  );
  for (const text of ["Checking", "Change focus", "Changed focus"])
    expect(
      screen.getByText(text).closest("[data-execution-reader]"),
    ).toBeNull();
  expect(
    screen
      .getAllByRole("button", { name: /Execution details/ })
      .map((button) => button.getAttribute("aria-expanded")),
  ).toEqual(["false", "false"]);
});

it("preserves an open execution reader when live rows become saved history", () => {
  const input = saved(
    previewInput("round", ["Prompt"]) as Schema<"TranscriptPart">[],
    0,
  );
  const turn = {
    turn_id: "round",
    input_position: 0,
    end_position: 1,
    preview: "Prompt",
  };
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[turn]}
      localInputs={[]}
      blocks={[
        { id: "live-progress", kind: "assistant", text: "Checking" },
        { id: "live-plan", kind: "thinking", text: "Plan" },
      ]}
    />,
  );
  const toggle = screen.getByRole("button", { name: /Execution details/ });
  fireEvent.click(toggle);
  const reader = screen.getByRole("region", { name: "Execution details" });
  view.rerender(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      blocks={[]}
      entries={[
        input,
        saved(
          [
            { kind: "assistant", text: "Checking" },
            { kind: "thinking", text: "Plan" },
            { kind: "assistant", text: "Done" },
          ],
          1,
        ),
      ]}
      turns={[{ ...turn, end_position: 2, final_position: 1 }]}
    />,
  );
  expect(screen.getByRole("button", { name: /Execution details/ })).toBe(
    toggle,
  );
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByRole("region", { name: "Execution details" })).toBe(
    reader,
  );
});

it.each(["desktop", "mobile"])(
  "keeps %s inspection open while a saved long turn completes loading",
  async (layout) => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: layout === "mobile" && query === "(max-width: 700px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const input = saved(
      previewInput("round", ["Prompt"]) as Schema<"TranscriptPart">[],
      0,
    );
    const steering = saved(
      previewInput("steer", ["Change direction"]) as Schema<"TranscriptPart">[],
      100,
    );
    const activity = saved([{ kind: "thinking", text: "Latest plan" }], 101);
    const output = saved([{ kind: "assistant", text: "Done" }], 102);
    let finish!: (value: unknown) => void;
    const GET = vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const transport = { client: { GET } } as unknown as Transport;
    const queries = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const turn = {
      turn_id: "round",
      input_position: 0,
      end_position: 1,
      preview: "Prompt",
    };
    const transcript = (checkpoint: boolean) => (
      <QueryClientProvider client={queries}>
        <TransportContext value={transport}>
          <ConversationTranscript
            threadId="one"
            loadDetails
            continuation={checkpoint ? "saved" : "initial"}
            entries={checkpoint ? [input, steering, activity, output] : [input]}
            turns={[
              checkpoint
                ? { ...turn, end_position: 103, final_position: 102 }
                : turn,
            ]}
            localInputs={[]}
            blocks={
              checkpoint
                ? []
                : [
                    {
                      id: "run:input:0",
                      kind: "user",
                      text: "Change direction",
                      metadata: steering.parts[0].metadata!,
                    },
                    { id: "live-plan", kind: "thinking", text: "Latest plan" },
                  ]
            }
          />
        </TransportContext>
      </QueryClientProvider>
    );
    const view = render(transcript(false));
    fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
    const reader = await screen.findByRole("region", {
      name: "Execution details",
    });
    const dialog = screen.queryByRole("dialog");
    view.rerender(transcript(true));
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("region", { name: "Execution details" })).toBe(
      reader,
    );
    if (layout === "mobile") expect(screen.getByRole("dialog")).toBe(dialog);
    await act(async () =>
      finish({
        data: {
          entries: [
            input,
            ...Array.from({ length: 99 }, (_, index) => saved([], index + 1)),
            steering,
            activity,
            output,
          ],
          next_cursor: null,
        },
      }),
    );
    await waitFor(() =>
      expect(view.container.textContent).not.toContain("Loading turn…"),
    );
    expect(screen.getByRole("region", { name: "Execution details" })).toBe(
      reader,
    );
    if (layout === "mobile") expect(screen.getByRole("dialog")).toBe(dialog);
    expect(GET).toHaveBeenCalledTimes(1);
    view.unmount();
    queries.clear();
  },
);

it.each([false, true])(
  "folds restored model connections into the latest execution segment (existing steps: %s)",
  (withSteps) => {
    const input = saved(
      previewInput("round", ["Prompt"]) as Schema<"TranscriptPart">[],
      0,
    );
    const props = {
      threadId: "one",
      entries: [input],
      turns: [
        {
          turn_id: "round",
          input_position: 0,
          end_position: 1,
          preview: "Prompt",
        },
      ],
      localInputs: [],
      blocks: withSteps
        ? [
            { id: "plan", kind: "thinking" as const, text: "Plan" },
            { id: "reply", kind: "assistant" as const, text: "Response" },
          ]
        : [{ id: "reply", kind: "assistant" as const, text: "Response" }],
    };
    const view = render(<ConversationTranscript {...props} />);
    const existing = screen.queryByRole("button", {
      name: /Execution details/,
    });
    view.rerender(
      <ConversationTranscript
        {...props}
        recovery={{ id: "retry-1", state: "resumed", retries: 1 }}
      />,
    );
    expect(
      screen.getAllByRole("button", { name: /Execution details/ }),
    ).toHaveLength(1);
    if (existing)
      expect(screen.getByRole("button", { name: /Execution details/ })).toBe(
        existing,
      );
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain("Model connection restored · 1 retry");
    expect(notice.closest("[data-execution-reader]")).toBe(
      screen.getByRole("region", { name: "Execution details" }),
    );
  },
);

it.each(["desktop", "mobile"])(
  "keeps a %s recovery-only inspector open when execution steps arrive",
  async (layout) => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: layout === "mobile" && query === "(max-width: 700px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const props = {
      threadId: "one",
      entries: [
        saved(
          previewInput("round", ["Prompt"]) as Schema<"TranscriptPart">[],
          0,
        ),
      ],
      turns: [
        {
          turn_id: "round",
          input_position: 0,
          end_position: 1,
          preview: "Prompt",
        },
      ],
      localInputs: [],
      recovery: { id: "retry-1", state: "resumed" as const, retries: 1 },
      blocks: [{ id: "reply", kind: "assistant" as const, text: "Checking" }],
    };
    const view = render(<ConversationTranscript {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
    const reader = await screen.findByRole("region", {
      name: "Execution details",
    });
    const dialog = screen.queryByRole("dialog");
    view.rerender(
      <ConversationTranscript
        {...props}
        blocks={[
          ...props.blocks,
          { id: "tool", kind: "tool", name: "view", text: "{}", done: true },
        ]}
      />,
    );
    expect(screen.getByRole("region", { name: "Execution details" })).toBe(
      reader,
    );
    if (layout === "mobile") expect(screen.getByRole("dialog")).toBe(dialog);
    expect(screen.getByRole("status").textContent).toContain(
      "Model connection restored · 1 retry",
    );
  },
);
