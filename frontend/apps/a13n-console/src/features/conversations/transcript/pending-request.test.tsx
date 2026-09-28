import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { fixtureRun, fixtureThread } from "./fixture";
import { RunFeedback } from "./pending-request";

const { post, accepted } = vi.hoisted(() => ({
  post: vi.fn(),
  accepted: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({
    http: { POST: post },
    workspace: () => ({ POST: post }),
  }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const run = fixtureRun({ status: "waiting" });
const thread = fixtureThread();
const successor = fixtureRun({ id: "run_3", status: "accepted" });
/** The resumed Run the Service answers a resume with. */
function resumes() {
  post.mockResolvedValue({ data: successor, response: new Response() });
}
function approval(
  tool_call_id: string,
  presentation: Schema["PendingItem"]["presentation"] = null,
): Schema["PendingItem"] {
  return {
    tool_call_id,
    kind: "approval",
    tool_name: `${tool_call_id} action`,
    arguments: { path: "/workspace/report" },
    presentation,
  };
}

it("requires an explicit decision for every approval before sending the complete response set", async () => {
  const user = userEvent.setup();
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        thread={thread}
        actions={[approval("first"), approval("second")]}
      />
    </QueryClientProvider>,
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.click(screen.getAllByRole("button", { name: "Approve once" })[0]!);
  expect(submit.disabled).toBe(true);
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getAllByRole("button", { name: /^Deny$/ })[1]!);
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(accepted).toHaveBeenCalledWith(successor));
  expect(post.mock.calls[0]![0]).toBe("/api/v1/runs/{run_id}/resume");
  expect(post.mock.calls[0]![1].body).toEqual({
    answers: [
      { action: "approve", tool_call_id: "first" },
      { action: "reject", tool_call_id: "second" },
    ],
  });
});

it("submits a bounded denial reason with the rest of the answers", async () => {
  const user = userEvent.setup();
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        run={run}
        thread={thread}
        actions={[
          approval("first", {
            target: "path: /workspace/report",
            risk: "high",
            reason: "Tool reviewer requires approval.",
          }),
        ]}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("path: /workspace/report")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Deny with reason" }));
  const reason = screen.getByRole("textbox", {
    name: "Denial reason (optional)",
  });
  await user.type(reason, "Sensitive destination");
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.answers).toEqual([
    {
      action: "reject",
      tool_call_id: "first",
      reason: "Sensitive destination",
    },
  ]);
});

it("falls back to JSON for malformed approval presentation", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        run={run}
        thread={thread}
        actions={[
          approval("first", {
            target: "path: /workspace",
            reason: { unexpected: true },
          }),
        ]}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Request details")).toBeTruthy();
  expect(screen.queryByText("Review reason")).toBeNull();
});

function question(
  questions: typeof questionPresentation = questionPresentation,
): Schema["PendingItem"] {
  return {
    tool_call_id: "question",
    kind: "user_input",
    tool_name: "ask_user_question",
    arguments: questions,
    presentation: null,
  };
}
/** A wait of questions alone is answered by the message that starts the next Run. */
function renderQuestions(questions = questionPresentation) {
  post.mockResolvedValue({
    data: { thread, entry: { id: "inb_2" }, run: successor },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        thread={thread}
        actions={[question(questions)]}
      />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
function answered() {
  expect(post.mock.calls[0]![0]).toBe("/api/v1/threads/{thread_id}/inbox");
  const body = post.mock.calls[0]![1].body;
  expect(body).toMatchObject({
    kind: "message",
    delivery: "next_run",
    agent_id: run.agent_id,
  });
  return body.payload.content;
}
const questionPresentation = {
  questions: [
    {
      header: "Business",
      question: "Which business?",
      options: [
        { label: "Retail", description: "Orders and returns" },
        { label: "Software", description: "Product support" },
      ],
    },
    {
      header: "Tools",
      question: "Which tools?",
      multiSelect: true,
      options: [
        { label: "Search", description: "Find answers" },
        { label: "Tickets", description: "Escalate issues" },
      ],
    },
  ],
};

it("answers questions with single and multiple selections in the exact answer envelope", async () => {
  const user = renderQuestions();
  expect(screen.getByText("Which business?")).toBeTruthy();
  expect(screen.getByText("Orders and returns")).toBeTruthy();
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("checkbox", { name: /Search/ }));
  await user.click(screen.getByRole("checkbox", { name: /Tickets/ }));
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(answered()).toEqual([
    {
      type: "json",
      value: {
        answers: {
          "Which business?": "Retail",
          "Which tools?": ["Search", "Tickets"],
        },
      },
    },
  ]);
  await waitFor(() => expect(accepted).toHaveBeenCalledWith(successor));
});

it("allows free text instead of an option and does not submit an empty answer", async () => {
  const user = renderQuestions({
    questions: [questionPresentation.questions[0]!],
  });
  await user.click(
    screen.getByRole("radio", { name: "Write your own answer" }),
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.type(
    screen.getByRole("textbox", { name: "Business: Your answer" }),
    "Travel support",
  );
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(answered()).toEqual([
    {
      type: "json",
      value: { answers: { "Which business?": "Travel support" } },
    },
  ]);
});

it("resumes a wait that mixes questions and approvals, leaving its questions unanswered", async () => {
  const user = userEvent.setup();
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        thread={thread}
        actions={[
          question({ questions: [questionPresentation.questions[0]!] }),
          approval("approval", {
            risk: "low",
            reason: "Tool policy requires approval.",
          }),
        ]}
      />
    </QueryClientProvider>,
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  // Only a message answers a question, and a mixed wait takes none.
  expect(screen.queryByRole("radio", { name: /Retail/ })).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "Response" }));
  expect(screen.queryByRole("option", { name: "Respond" })).toBeNull();
  await user.click(
    await screen.findByRole("option", { name: "Continue without a response" }),
  );
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Approve once" }));
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body).toEqual({
    answers: [{ action: "approve", tool_call_id: "approval" }],
  });
});
