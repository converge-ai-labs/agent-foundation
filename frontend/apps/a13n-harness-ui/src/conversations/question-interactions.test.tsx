// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Schema, Transport } from "../transport/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { DecisionForm, useDecisionPlacement } from "./decisions";
import { ConversationTranscript } from "./transcript";
import type { DisplayBlock } from "./stream";
import { previewInput } from "./local-input";

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
});
const args = {
  questions: [
    {
      header: "Scope",
      question: "Which scope?",
      options: [
        { label: "WebUI", description: "Only the browser surface" },
        { label: "All", description: "Every surface" },
      ],
    },
  ],
};
const answer = { answers: { "Which scope?": "WebUI" } };
const saved = (
  position: number,
  parts: Schema<"TranscriptPart">[],
): Schema<"TranscriptEntry"> => ({ position, message_kind: "request", parts });
const input = saved(
  0,
  previewInput("round", ["Original input"]) as Schema<"TranscriptPart">[],
);
const call = (id = "q"): Schema<"TranscriptPart"> => ({
  kind: "tool_call",
  tool_call_id: id,
  tool_name: "ask_user_question",
  value: args,
});
const result = (id = "q"): Schema<"TranscriptPart"> => ({
  kind: "tool_result",
  tool_call_id: id,
  tool_name: "ask_user_question",
  value: answer,
  outcome: "success",
});
const turn = {
  turn_id: "round",
  input_position: 0,
  end_position: 2,
  preview: "Original input",
};
const liveCall: DisplayBlock = {
  id: "run-one:q",
  toolCallId: "q",
  kind: "tool",
  name: "ask_user_question",
  text: JSON.stringify(args),
  done: true,
};

it("keeps question receipts between independently keyed execution stretches without adding an input or Turn", () => {
  const entries = [
    input,
    saved(1, [
      { kind: "thinking", text: "Before question" },
      call(),
      { kind: "thinking", text: "After question" },
    ]),
    saved(2, [result(), { kind: "assistant", text: "Final answer" }]),
  ];
  const { container } = render(
    <ConversationTranscript
      threadId="one"
      entries={entries}
      turns={[{ ...turn, end_position: 3, final_position: 2 }]}
      localInputs={[]}
      blocks={[]}
    />,
  );
  const receipt = screen.getByRole("region", { name: "Answers" });
  expect(receipt.closest("[data-execution-reader]")).toBeNull();
  expect(receipt.closest("[hidden]")).toBeNull();
  expect(screen.getByText("Only the browser surface")).toBeTruthy();
  expect(screen.queryByText("Every surface")).toBeNull();
  expect(container.querySelectorAll("[data-turn-id]")).toHaveLength(1);
  expect(container.querySelectorAll("[data-message-id]")).toHaveLength(1);
  const toggles = screen.getAllByRole("button", { name: /Execution details/ });
  expect(toggles).toHaveLength(2);
  fireEvent.click(toggles[0]);
  expect(toggles.map((item) => item.getAttribute("aria-expanded"))).toEqual([
    "true",
    "false",
  ]);
  expect(screen.getByText("Before question")).toBeTruthy();
  expect(screen.queryByText("After question")).toBeNull();
  fireEvent.click(toggles[1]);
  expect(toggles.map((item) => item.getAttribute("aria-expanded"))).toEqual([
    "true",
    "true",
  ]);
  expect(container.textContent).toMatch(
    /Before question.*Which scope\?.*WebUI.*After question.*Final answer/s,
  );
});

it("correlates a resume result with its saved call and preserves disclosures through checkpoint cutover", () => {
  const entries = [
    input,
    saved(1, [{ kind: "thinking", text: "Before question" }, call()]),
  ];
  const blocks: DisplayBlock[] = [
    {
      id: "run-two:q",
      toolCallId: "q",
      kind: "tool",
      text: "",
      result: JSON.stringify(answer),
      outcome: "success",
      done: true,
    },
    { id: "run-two:plan", kind: "thinking", text: "After question" },
  ];
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={entries}
      turns={[turn]}
      blocks={blocks}
      localInputs={[]}
    />,
  );
  expect(screen.getAllByRole("region", { name: "Answers" })).toHaveLength(1);
  expect(screen.getByText("Only the browser surface")).toBeTruthy();
  const receipt = screen.getByRole("region", { name: "Answers" });
  const details = screen.getByRole("button", { name: "Questions & details" });
  fireEvent.click(details);
  const execution = screen.getAllByRole("button", {
    name: /Execution details/,
  });
  fireEvent.click(execution[1]);
  view.rerender(
    <ConversationTranscript
      threadId="one"
      entries={[
        ...entries,
        saved(2, [result(), { kind: "thinking", text: "After question" }]),
      ]}
      turns={[{ ...turn, end_position: 3 }]}
      blocks={[]}
      localInputs={[]}
    />,
  );
  expect(screen.getByRole("region", { name: "Answers" })).toBe(receipt);
  expect(details.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByRole("button", { name: /Execution details/ })).toEqual(
    execution,
  );
  expect(execution[1].getAttribute("aria-expanded")).toBe("true");
});

it("keeps one mixed-batch form mounted when pending calls become saved history", () => {
  const pending = {
    requestIds: ["q", "q2"],
    content: (
      <section aria-label="Pending decisions">
        <input aria-label="Draft answer" defaultValue="" />
        <button>Submit batch</button>
      </section>
    ),
  };
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[{ ...turn, end_position: 1 }]}
      blocks={[liveCall, { ...liveCall, id: "run-one:q2", toolCallId: "q2" }]}
      localInputs={[]}
      pending={pending}
    />,
  );
  const field = screen.getByRole("textbox");
  fireEvent.change(field, { target: { value: "Keep this draft" } });
  expect(screen.queryByText("Which scope?")).toBeNull();
  expect(screen.getAllByRole("button", { name: "Submit batch" })).toHaveLength(
    1,
  );
  expect(field.closest("[data-execution-reader]")).toBeNull();
  view.rerender(
    <ConversationTranscript
      threadId="one"
      entries={[input, saved(1, [call(), call("q2")])]}
      turns={[turn]}
      blocks={[]}
      localInputs={[]}
      pending={pending}
    />,
  );
  expect(screen.getByRole("textbox")).toBe(field);
  expect((field as HTMLInputElement).value).toBe("Keep this draft");
  view.rerender(
    <ConversationTranscript
      threadId="one"
      entries={[
        input,
        saved(1, [call(), call("q2")]),
        saved(2, [result(), result("q2")]),
      ]}
      turns={[{ ...turn, end_position: 3 }]}
      blocks={[]}
      localInputs={[]}
    />,
  );
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getAllByRole("region", { name: "Answers" })).toHaveLength(2);
});

it("renders a pending batch before history arrives without resetting its draft when the call is hydrated", () => {
  const pending = {
    requestIds: ["q"],
    content: <input aria-label="Draft answer" />,
  };
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[turn]}
      blocks={[]}
      localInputs={[]}
      pending={pending}
    />,
  );
  const field = screen.getByRole("textbox");
  fireEvent.change(field, { target: { value: "Draft" } });
  view.rerender(
    <ConversationTranscript
      threadId="one"
      entries={[input, saved(1, [{ kind: "thinking", text: "Plan" }, call()])]}
      turns={[turn]}
      blocks={[]}
      localInputs={[]}
      pending={pending}
    />,
  );
  expect(screen.getByRole("textbox")).toBe(field);
  expect((field as HTMLInputElement).value).toBe("Draft");
});

it("does not correlate equal question text or provider call IDs", () => {
  render(
    <ConversationTranscript
      threadId="one"
      entries={[input, saved(1, [call("other"), call()])]}
      turns={[turn]}
      blocks={[
        {
          ...liveCall,
          id: "run-two:q",
          text: "",
          result: JSON.stringify(answer),
          outcome: "success",
        },
        {
          ...liveCall,
          id: "provider:q",
          provider: "openai",
          text: "{}",
          result: "Provider result",
        },
      ]}
      localInputs={[]}
    />,
  );
  expect(screen.getAllByRole("region", { name: "Answers" })).toHaveLength(1);
  expect(screen.getAllByRole("region", { name: "Question" })).toHaveLength(1);
  expect(screen.getByText("Awaiting response")).toBeTruthy();
});

it.each(["failed", "denied", "interrupted"] as const)(
  "keeps %s questions visible without inferring an answer",
  (outcome) => {
    render(
      <ConversationTranscript
        threadId="one"
        entries={[
          input,
          saved(1, [call()]),
          saved(2, [{ ...result(), outcome, value: "Response window ended" }]),
        ]}
        turns={[{ ...turn, end_position: 3 }]}
        blocks={[]}
        localInputs={[]}
      />,
    );
    expect(screen.queryByRole("region", { name: "Answers" })).toBeNull();
    const question = screen.getByRole("region", { name: "Question" });
    expect(question.closest("[data-execution-reader]")).toBeNull();
    expect(screen.getByText("Which scope?")).toBeTruthy();
    expect(screen.getByText("Not answered")).toBeTruthy();
    expect(screen.getByText("Response window ended")).toBeTruthy();
  },
);

it.each(["empty history", "partial batch"])(
  "retains drafts and uncertain submission state as %s is hydrated",
  async (initial) => {
    const POST = vi.fn().mockRejectedValue(new Error("Connection lost"));
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const transport = { client: { POST } } as unknown as Transport;
    const batch: Schema<"DecisionBatchView"> = {
      continuation_id: "C1",
      requests: [
        {
          kind: "question",
          request_id: "q",
          tool_name: "ask_user_question",
          questions: [{ ...args.questions[0], multi_select: false }],
        },
      ],
    };
    function Harness({ loaded }: { loaded: boolean }) {
      const decisions = useDecisionPlacement(
        <DecisionForm threadId="one" batch={batch} reconcile={() => {}} />,
      );
      return (
        <>
          <ConversationTranscript
            threadId="one"
            entries={
              loaded
                ? [input, saved(1, [call(), call("q2")])]
                : initial === "empty history"
                  ? []
                  : [input, saved(1, [call("q2")])]
            }
            turns={loaded || initial === "partial batch" ? [turn] : []}
            blocks={[]}
            localInputs={[]}
            pending={{ requestIds: ["q", "q2"], content: decisions.slot }}
          />
          {decisions.portal}
        </>
      );
    }
    const body = (loaded: boolean) => (
      <QueryClientProvider client={client}>
        <TransportContext value={transport}>
          <Harness loaded={loaded} />
        </TransportContext>
      </QueryClientProvider>
    );
    const view = render(body(false));
    const field = screen.getByRole("textbox", {
      name: "Or write your own answer",
    });
    fireEvent.change(field, { target: { value: "Retain this answer" } });
    view.rerender(body(true));
    expect(
      screen.getByRole("textbox", { name: "Or write your own answer" }),
    ).toBe(field);
    expect((field as HTMLInputElement).value).toBe("Retain this answer");
    fireEvent.click(screen.getByRole("button", { name: "Submit responses" }));
    await screen.findByText(/Acknowledgement unavailable/);
    view.rerender(body(false));
    view.rerender(body(true));
    expect(screen.getByText(/Acknowledgement unavailable/)).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Submit responses",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(POST).toHaveBeenCalledOnce();
    expect(POST.mock.calls[0][1].body.responses[0].answers).toEqual({
      "Which scope?": "Retain this answer",
    });
  },
);

it("keeps inline child questions and replies inside execution without replacing a root question with the same native ID", () => {
  const childArgs = {
    questions: [{ ...args.questions[0], question: "Child scope?" }],
  };
  const blocks: DisplayBlock[] = [
    liveCall,
    {
      ...liveCall,
      id: "run-one:child:q",
      subagentRunId: "child",
      text: JSON.stringify(childArgs),
      result: JSON.stringify({ answers: { "Child scope?": "All" } }),
      outcome: "success",
    },
    {
      id: "run-one:child:reply",
      kind: "assistant",
      text: "Child-only reply",
      subagentRunId: "child",
    },
    { id: "run-one:reply", kind: "assistant", text: "Root reply" },
  ];
  const view = render(
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[{ ...turn, end_position: 1 }]}
      localInputs={[]}
      blocks={blocks}
    />,
  );
  expect(
    screen.getByText("Which scope?").closest("[data-execution-reader]"),
  ).toBeNull();
  expect(screen.queryByText("Child-only reply")).toBeNull();
  expect(
    screen.getByText("Root reply").closest("[data-execution-reader]"),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
  expect(
    screen.getByText("Child-only reply").closest("[data-execution-reader]"),
  ).not.toBeNull();
  expect(screen.getByText("Child scope?")).toBeTruthy();
  expect(screen.getByText("Which scope?")).toBeTruthy();
  view.rerender(
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[{ ...turn, end_position: 1 }]}
      localInputs={[]}
      blocks={blocks}
      pending={{ requestIds: ["q"], content: <div>Root pending decision</div> }}
    />,
  );
  expect(
    screen
      .getByText("Root pending decision")
      .closest("[data-execution-reader]"),
  ).toBeNull();
  expect(screen.getByText("Child scope?")).toBeTruthy();
});
