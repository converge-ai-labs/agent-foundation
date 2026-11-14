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
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import type { DisplayBlock } from "./stream";
import { ConversationTranscript } from "./transcript";
import { MessageBranches } from "./message-branch";
import { useHistory } from "./queries";
import { previewInput, type LocalInput } from "./local-input";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const local: LocalInput = {
  id: "round",
  action: "send",
  state: "accepted",
  parts: previewInput("round", ["Prompt"]),
};
const input: Schema<"TranscriptEntry"> = {
  position: 0,
  message_kind: "request",
  parts: local.parts as Schema<"TranscriptPart">[],
};
const output: Schema<"TranscriptEntry"> = {
  position: 1,
  message_kind: "response",
  parts: [{ kind: "assistant", text: "Visible reply" }],
};
const turn = {
  turn_id: "round",
  input_position: 0,
  end_position: 1,
  preview: "Prompt",
};
const props = {
  entries: [input],
  turns: [turn],
  localInputs: [],
  threadId: "one",
  continuation: "C0",
};
const readingAnchor = (node: HTMLElement) =>
  node.closest<HTMLElement>("[data-reading-anchor]")?.dataset.readingAnchor;

it("shows response time only on the final reply of a completed run", () => {
  const timestamp = "2026-11-11T05:57:43.000Z";
  const entries = [
    input,
    { ...output, timestamp },
    {
      ...output,
      position: 2,
      timestamp,
      parts: [{ kind: "assistant" as const, text: "Final reply" }],
    },
  ];
  const closing = { ...turn, end_position: 3, output_position: 2 };
  const view = render(
    <ConversationTranscript
      {...props}
      entries={entries}
      turns={[closing]}
      blocks={[]}
    />,
  );
  expect(view.container.querySelector("time")).toBeNull();
  view.rerender(
    <ConversationTranscript
      {...props}
      entries={entries}
      turns={[{ ...closing, final_position: 2 }]}
      blocks={[]}
    />,
  );
  expect(view.container.querySelectorAll("time")).toHaveLength(1);
  expect(
    screen.getByText("Final reply").closest("section")?.querySelector("time")
      ?.dateTime,
  ).toBe(timestamp);
});

it("branches from an imported reply without a timestamp", () => {
  const create = vi.fn();
  const imported = {
    ...turn,
    turn_id: "entry:0",
    timestamp: null,
    end_position: 2,
    output_position: 1,
  };
  render(
    <MessageBranches
      value={{
        busy: false,
        pending: false,
        create,
        points: { "entry:0": { edit: true, branch: true } },
      }}
    >
      <ConversationTranscript
        {...props}
        entries={[input, output]}
        turns={[imported]}
        blocks={[]}
      />
    </MessageBranches>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Branch in new chat" }));
  expect(create).toHaveBeenCalledWith("entry:0");
});

it("retains live output nodes and reading anchors through repeated saves", () => {
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[
        { id: "run:message-1", kind: "assistant", text: "Visible reply" },
      ]}
    />,
  );
  const before = screen.getByText("Visible reply");
  const anchor = readingAnchor(before);
  for (const continuation of ["C1", "C2"]) {
    view.rerender(
      <ConversationTranscript
        {...props}
        entries={[input, output]}
        turns={[{ ...turn, end_position: 2 }]}
        blocks={[]}
        continuation={continuation}
      />,
    );
    expect(screen.getByText("Visible reply")).toBe(before);
    expect(readingAnchor(before)).toBe(anchor);
  }
});

it.each([
  { mobile: false, sender: true },
  { mobile: true, sender: true },
  { mobile: false, sender: false },
  { mobile: true, sender: false },
])(
  "retains first-turn content and the open execution reader during slow multi-page saving ($mobile, sender=$sender)",
  async ({ mobile, sender }) => {
    vi.stubGlobal("matchMedia", () => ({
      matches: mobile,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const queries = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const plan: Schema<"TranscriptEntry"> = {
      position: 1,
      message_kind: "response",
      parts: [{ kind: "thinking", text: "Plan" }],
    };
    const reply = { ...output, position: 2 };
    const savedTurn = { ...turn, turn_id: "attempt-one", end_position: 3 };
    const blocks: DisplayBlock[] = [
      {
        id: "round:input:0",
        kind: "user",
        text: "Prompt",
        metadata: local.parts[0].metadata ?? undefined,
        inputSource: "user",
        inputGroup: "attempt-one",
      },
      { id: "run:plan", kind: "thinking", text: "Plan" },
      { id: "run:reply", kind: "assistant", text: "Visible reply" },
    ];
    let finish!: (value: unknown) => void;
    const GET = vi
      .fn()
      .mockResolvedValueOnce({
        data: {
          continuation_id: "C0",
          entries: [],
          turns: [],
          next_cursor: null,
        },
      })
      .mockResolvedValueOnce({
        data: {
          continuation_id: "C1",
          entries: [reply],
          boundary_entries: [input],
          turns: [savedTurn],
          next_cursor: "older",
          earlier_turns_cursor: null,
        },
      })
      .mockResolvedValueOnce({
        data: { entries: [reply], next_cursor: "older" },
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    function Transcript({ continuation }: { continuation: string }) {
      const history = useHistory("one", continuation, true);
      const page = history.data?.pages[0];
      return (
        <ConversationTranscript
          threadId="one"
          localInputs={sender ? [local] : []}
          loadDetails
          entries={page?.entries ?? []}
          turns={page?.turns ?? []}
          blocks={page?.continuation_id === "C1" ? [] : blocks}
          continuation={page?.continuation_id}
        />
      );
    }
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queries}>
        <TransportContext value={{ client: { GET } } as unknown as Transport}>
          {children}
        </TransportContext>
      </QueryClientProvider>
    );
    const view = render(<Transcript continuation="C0" />, { wrapper });
    await waitFor(() =>
      expect(
        queries.getQueryData(["thread", "one", "history", "C0"]),
      ).toBeTruthy(),
    );
    const toggle = screen.getByRole("button", { name: /Work log/ });
    fireEvent.click(toggle);
    const reader = screen.getByRole("region", { name: "Work log" });
    const prompt = screen.getByText("Prompt");
    const reasoning = screen.getByText("Plan");
    const answer = screen.getByText("Visible reply");
    const anchor = readingAnchor(answer);
    const assertRetained = () => {
      expect(screen.queryByText("Loading turn…")).toBeNull();
      expect(screen.getByText("Prompt")).toBe(prompt);
      expect(screen.getByText("Plan")).toBe(reasoning);
      expect(screen.getByText("Visible reply")).toBe(answer);
      expect(readingAnchor(answer)).toBe(anchor);
      expect(screen.getByRole("region", { name: "Work log" })).toBe(reader);
      expect(toggle.isConnected).toBe(true);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
    };
    view.rerender(<Transcript continuation="C1" />);
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(4));
    assertRetained();
    await act(async () =>
      finish({ data: { entries: [input, plan], next_cursor: null } }),
    );
    await waitFor(() =>
      expect(
        queries.getQueryData(["thread", "one", "history", "C1"]),
      ).toBeTruthy(),
    );
    assertRetained();
    expect(GET).toHaveBeenCalledTimes(4);
    view.unmount();
    queries.clear();
  },
);

it("preserves distinct repeated text slots without merging messages", () => {
  const blocks: DisplayBlock[] = [
    { id: "run:first", kind: "assistant", text: "Repeated" },
    { id: "run:plan", kind: "thinking", text: "Plan" },
    { id: "run:second", kind: "assistant", text: "Repeated" },
  ];
  const view = render(<ConversationTranscript {...props} blocks={blocks} />);
  const before = screen.getAllByText("Repeated");
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        {
          ...output,
          parts: [
            { kind: "assistant", text: "Repeated" },
            { kind: "thinking", text: "Plan" },
            { kind: "assistant", text: "Repeated" },
          ],
        },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  const after = screen.getAllByText("Repeated");
  expect(after).toHaveLength(2);
  expect(after[0]).toBe(before[0]);
  expect(after[1]).toBe(before[1]);
  expect(readingAnchor(after[0])).not.toBe(readingAnchor(after[1]));
});

it("does not remount an unchanged saved part when a sibling changes, but replaces changed saved text", () => {
  const entries = [
    input,
    {
      ...output,
      parts: [
        { kind: "assistant" as const, text: "Visible reply" },
        { kind: "thinking" as const, text: "First plan" },
      ],
    },
  ];
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={entries}
      turns={[{ ...turn, end_position: 2 }]}
    />,
  );
  const answer = screen.getByText("Visible reply");
  fireEvent.click(screen.getByRole("button", { name: /Work log/ }));
  const reasoning = screen.getByRole("button", { name: "Thought" });
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        {
          ...output,
          parts: [output.parts[0], { kind: "thinking", text: "Changed plan" }],
        },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  expect(screen.getByText("Visible reply")).toBe(answer);
  expect(screen.getByRole("button", { name: "Thought" })).not.toBe(reasoning);
});

it("does not transfer live state to changed saved text or to another input boundary", () => {
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[{ id: "run:reply", kind: "assistant", text: "Visible reply" }]}
    />,
  );
  const answer = screen.getByText("Visible reply");
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        { ...output, parts: [{ kind: "assistant", text: "Different reply" }] },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  expect(screen.getByText("Different reply")).not.toBe(answer);
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[{ id: "run:new", kind: "assistant", text: "Visible reply" }]}
    />,
  );
  const next = screen.getByText("Visible reply");
  const other = {
    ...input,
    parts: previewInput("other", [
      "Other prompt",
    ]) as Schema<"TranscriptPart">[],
  };
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[other, output]}
      turns={[{ ...turn, turn_id: "other", end_position: 2 }]}
      continuation="C2"
    />,
  );
  expect(screen.getByText("Visible reply")).not.toBe(next);
});

it("never assigns a still-live message identity to matching saved text", () => {
  const blocks: DisplayBlock[] = [
    { id: "run:reply", kind: "assistant", text: "Visible reply" },
  ];
  const view = render(<ConversationTranscript {...props} blocks={blocks} />);
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={blocks}
      entries={[input, output]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  const messages = screen.getAllByText("Visible reply");
  expect(messages).toHaveLength(2);
  expect(readingAnchor(messages[0])).not.toBe(readingAnchor(messages[1]));
});

it.each([true, false])(
  "keeps an observer's second turn separate from completed work (source metadata=%s)",
  (withSource) => {
    const parts = withSource
      ? previewInput("second-source", ["Second prompt"])
      : [{ kind: "user" as const, text: "Second prompt" }];
    const firstEntries: Schema<"TranscriptEntry">[] = [
      input,
      {
        ...output,
        parts: [
          { kind: "assistant", text: "Old commentary" },
          { kind: "thinking", text: "Old plan" },
        ],
      },
      { ...output, position: 2 },
    ];
    const firstTurn = { ...turn, end_position: 3, final_position: 2 };
    const secondTurn = {
      turn_id: "second-attempt",
      input_position: 3,
      end_position: 6,
      preview: "Second prompt",
    };
    const blocks: DisplayBlock[] = [
      {
        id: "second-item",
        kind: "user",
        text: "Second prompt",
        metadata: parts[0].metadata ?? undefined,
        inputSource: "user",
        inputGroup: "second-attempt",
      },
      { id: "second-plan", kind: "thinking", text: "Second plan" },
      { id: "second-answer", kind: "assistant", text: "Second reply" },
    ];
    const view = render(
      <ConversationTranscript
        {...props}
        entries={firstEntries}
        turns={[firstTurn]}
        blocks={blocks}
      />,
    );
    const oldAnswer = screen.getByText("Visible reply");
    const prompt = screen.getByText("Second prompt");
    const answer = screen.getByText("Second reply");
    expect(prompt.closest("[data-turn-id]")?.getAttribute("data-turn-id")).toBe(
      "second-attempt",
    );
    expect(screen.queryByText("Old commentary")).toBeNull();
    const logs = screen.getAllByRole("button", { name: /Work log/ });
    fireEvent.click(logs[1]);
    const reader = screen.getByRole("region", { name: "Work log" });
    view.rerender(
      <ConversationTranscript
        {...props}
        continuation="C1"
        entries={[
          ...firstEntries,
          {
            position: 3,
            message_kind: "request",
            parts: parts as Schema<"TranscriptPart">[],
          },
          {
            position: 4,
            message_kind: "response",
            parts: [{ kind: "thinking", text: "Second plan" }],
          },
          {
            position: 5,
            message_kind: "response",
            parts: [{ kind: "assistant", text: "Second reply" }],
          },
        ]}
        turns={[firstTurn, secondTurn]}
        blocks={[]}
      />,
    );
    expect(screen.getByText("Second prompt")).toBe(prompt);
    expect(screen.getByText("Second reply")).toBe(answer);
    expect(screen.getByText("Visible reply")).toBe(oldAnswer);
    expect(screen.getByRole("region", { name: "Work log" })).toBe(reader);
    expect(screen.queryByText("Old commentary")).toBeNull();
  },
);

it("keeps accepted preview identity when native turn identity arrives", () => {
  const view = render(
    <ConversationTranscript
      {...props}
      entries={[]}
      turns={[]}
      localInputs={[local]}
      blocks={[]}
    />,
  );
  const prompt = screen.getByText("Prompt");
  view.rerender(
    <ConversationTranscript
      {...props}
      entries={[]}
      turns={[]}
      localInputs={[local]}
      blocks={[
        {
          id: "native-item",
          kind: "user",
          text: "Prompt",
          metadata: local.parts[0].metadata ?? undefined,
          inputSource: "user",
          inputGroup: "native-attempt",
        },
      ]}
    />,
  );
  expect(screen.getByText("Prompt")).toBe(prompt);
  expect(prompt.closest("[data-turn-id]")?.getAttribute("data-turn-id")).toBe(
    "native-attempt",
  );
  view.rerender(
    <ConversationTranscript
      {...props}
      turns={[{ ...turn, turn_id: "native-attempt" }]}
      localInputs={[local]}
      blocks={[]}
      continuation="C1"
    />,
  );
  expect(screen.getByText("Prompt")).toBe(prompt);
});

it.each(["steering", "child", "resume"])(
  "does not create a root turn for %s output",
  (kind) => {
    const blocks: DisplayBlock[] =
      kind === "resume"
        ? []
        : [
            {
              id: "additional-input",
              kind: "user",
              text: "Additional input",
              inputGroup: "other-group",
              inputSource: kind === "steering" ? "steering" : "user",
              subagentRunId: kind === "child" ? "child-run" : undefined,
            },
          ];
    const view = render(
      <ConversationTranscript
        {...props}
        blocks={[
          ...blocks,
          { id: "continued", kind: "assistant", text: "Continued reply" },
        ]}
      />,
    );
    expect(view.container.querySelectorAll("[data-turn-id]")).toHaveLength(1);
    expect(
      screen
        .getByText("Continued reply")
        .closest("[data-turn-id]")
        ?.getAttribute("data-turn-id"),
    ).toBe("round");
  },
);
