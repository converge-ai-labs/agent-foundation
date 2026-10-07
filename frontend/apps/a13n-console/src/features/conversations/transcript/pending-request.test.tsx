import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { fixtureRun } from "./fixture";
import { RunFeedback } from "./pending-request";

const { post, get, accepted } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  accepted: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({
    http: { POST: post },
    workspace: () => ({ POST: post, GET: get }),
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
beforeEach(() => {
  get.mockResolvedValue({
    data: { run_id: run.id, status: "waiting", answers: [], successor: null },
    response: new Response(),
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const run = fixtureRun({ status: "waiting" });
const successor = fixtureRun({ id: "run_3", status: "accepted" });
/** The resumed Run the Service answers a resume with. */
function resumes() {
  post.mockResolvedValue({
    data: { run_id: run.id, status: "resumed", answers: [], successor },
    response: new Response(),
  });
}
function approval(
  tool_call_id: string,
  presentation: Schema["PendingCall"]["presentation"] = null,
): Schema["PendingCall"] {
  return {
    tool_call_id,
    tool_name: `${tool_call_id} action`,
    arguments: { path: "/workspace/report" },
    presentation,
  };
}

it("saves a single answer without resuming, restores it after refresh, then submits only the remaining answer", async () => {
  const user = userEvent.setup();
  const pending = {
    approvals: [approval("first"), approval("second")],
    calls: [],
  };
  const saved = {
    run_id: run.id,
    status: "waiting",
    answers: [
      {
        answer: { approvals: { first: { action: "approve" } }, calls: {} },
        answered_by_id: "user",
        created_at: "2026-10-07T00:00:00Z",
      },
    ],
    successor: null,
  };
  post.mockResolvedValue({ data: saved, response: new Response() });
  const view = () => (
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback accepted={accepted} run={run} pending={pending} />
    </QueryClientProvider>
  );
  const first = render(view());
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "Approve once" })[0],
    ).toHaveProperty("disabled", false),
  );
  await user.click(screen.getAllByRole("button", { name: "Approve once" })[0]!);
  await user.click(
    screen.getAllByRole("button", { name: "Save response" })[0]!,
  );
  await screen.findByText(
    "Response saved. Waiting for the remaining responses.",
  );
  expect(accepted).not.toHaveBeenCalled();
  expect(post.mock.calls[0]![1].body).toEqual({
    approvals: { first: { action: "approve" } },
    calls: {},
  });
  first.unmount();
  get.mockResolvedValue({ data: saved, response: new Response() });
  render(view());
  await screen.findByText(
    "Response saved. Waiting for the remaining responses.",
  );
  expect(screen.getAllByRole("button", { name: "Approve once" })).toHaveLength(
    1,
  );
  resumes();
  await user.click(screen.getByRole("button", { name: /^Deny$/ }));
  await user.click(screen.getByRole("button", { name: "Save response" }));
  await waitFor(() => expect(accepted).toHaveBeenCalledWith(successor));
  expect(post.mock.calls[1]![0]).toBe("/api/v1/runs/{run_id}/answers");
  expect(post.mock.calls[1]![1].body).toEqual({
    approvals: { second: { action: "deny" } },
    calls: {},
  });
});

it("saves a bounded denial reason", async () => {
  const user = userEvent.setup();
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        run={run}
        pending={{
          calls: [],
          approvals: [
            approval("first", {
              target: "path: /workspace/report",
              risk: "high",
              reason: "Tool reviewer requires approval.",
            }),
          ],
        }}
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
  await user.click(screen.getByRole("button", { name: "Save response" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.approvals).toEqual({
    first: { action: "deny", reason: "Sensitive destination" },
  });
});

it("falls back to JSON for malformed approval presentation", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        run={run}
        pending={{
          calls: [],
          approvals: [
            approval("first", {
              target: "path: /workspace",
              reason: { unexpected: true },
            }),
          ],
        }}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Request details")).toBeTruthy();
  expect(screen.queryByText("Review reason")).toBeNull();
});

function question(
  questions: typeof questionPresentation = questionPresentation,
): Schema["PendingCall"] {
  return {
    tool_call_id: "question",
    tool_name: "ask_user_question",
    arguments: questions,
    presentation: null,
  };
}
/** Render the durable pending call, including after a page refresh. */
function renderQuestions(questions = questionPresentation) {
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        pending={{ approvals: [], calls: [question(questions)] }}
      />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
function answered() {
  expect(post.mock.calls[0]![0]).toBe("/api/v1/runs/{run_id}/answers");
  expect(post.mock.calls[0]![1].params.path).toEqual({ run_id: run.id });
  return post.mock.calls[0]![1].body.calls;
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
    name: "Save response",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("checkbox", { name: /Search/ }));
  await user.click(screen.getByRole("checkbox", { name: /Tickets/ }));
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(answered()).toEqual({
    question: {
      status: "returned",
      value: {
        answers: {
          "Which business?": "Retail",
          "Which tools?": ["Search", "Tickets"],
        },
      },
    },
  });
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
    name: "Save response",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.type(
    screen.getByRole("textbox", { name: "Business: Your answer" }),
    "Travel support",
  );
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(answered()).toEqual({
    question: {
      status: "returned",
      value: { answers: { "Which business?": "Travel support" } },
    },
  });
});

it("saves a question independently of an unanswered approval", async () => {
  const user = userEvent.setup();
  post.mockResolvedValue({
    data: { run_id: run.id, status: "waiting", answers: [], successor: null },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        pending={{
          calls: [
            question({ questions: [questionPresentation.questions[0]!] }),
          ],
          approvals: [approval("approval")],
        }}
      />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  const buttons = screen.getAllByRole("button", { name: "Save response" });
  expect(buttons[0]).toHaveProperty("disabled", true);
  await user.click(buttons[1]!);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body).toEqual({
    approvals: {},
    calls: {
      question: {
        status: "returned",
        value: { answers: { "Which business?": "Retail" } },
      },
    },
  });
  expect(accepted).not.toHaveBeenCalled();
});

it("only skips a question after an explicit choice and submission", async () => {
  const user = renderQuestions();
  await user.click(
    screen.getByRole("button", { name: "Continue without a response" }),
  );
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save response" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(answered()).toEqual({
    question: { status: "failed", message: "No response was given" },
  });
});

it("preserves the question response and exact request after a stale-wait conflict", async () => {
  const user = renderQuestions({
    questions: [questionPresentation.questions[0]!],
  });
  post.mockResolvedValue({
    error: { error: { code: "conflict", message: "This wait has changed." } },
    response: new Response(null, { status: 409 }),
  });
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  await user.click(screen.getByRole("button", { name: "Save response" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("radio", { name: /Retail/ })).toHaveProperty(
    "checked",
    true,
  );
  expect(accepted).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save response" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]).toEqual(post.mock.calls[0]);
  expect(Object.keys(answered())).toEqual(["question"]);
});

it.each([false, true])(
  "submits a custom human tool result or explicit failure (failure: %s)",
  async (failed) => {
    const user = userEvent.setup();
    resumes();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RunFeedback
          run={run}
          accepted={accepted}
          pending={{
            approvals: [],
            calls: [
              {
                tool_call_id: "review",
                tool_name: "review_invoice",
                arguments: { invoice: 7 },
                presentation: null,
              },
            ],
          }}
        />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Request details" }));
    expect(screen.getByText(/"invoice": 7/)).toBeTruthy();
    await user.click(screen.getByRole("combobox", { name: "Response" }));
    await user.click(
      await screen.findByRole("option", {
        name: failed ? "Report tool failure" : "Return tool result",
      }),
    );
    if (failed) {
      await user.click(screen.getByRole("button", { name: "Save response" }));
      expect((await screen.findByRole("alert")).textContent).toContain(
        "Enter a failure reason.",
      );
      expect(post).not.toHaveBeenCalled();
      await user.type(
        screen.getByRole("textbox", { name: "Failure reason" }),
        "Reviewer unavailable",
      );
    } else {
      await user.click(screen.getByRole("button", { name: "Save response" }));
      await screen.findByRole("alert");
      expect(post).not.toHaveBeenCalled();
      await user.click(
        screen.getByRole("textbox", { name: "Response (JSON)" }),
      );
      await user.paste('{"approved":false}');
    }
    await user.click(screen.getByRole("button", { name: "Save response" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]![1].body).toEqual({
      approvals: {},
      calls: {
        review: failed
          ? { status: "failed", message: "Reviewer unavailable" }
          : { status: "returned", value: { approved: false } },
      },
    });
  },
);

it("disables unsaved responses when the server reports the wait has closed", async () => {
  get.mockResolvedValue({
    data: { run_id: run.id, status: "closed", answers: [], successor: null },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        run={run}
        pending={{ approvals: [approval("first")], calls: [] }}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  await screen.findByText("This request is no longer waiting for responses.");
  expect(
    screen.getByRole("button", { name: "Approve once" }).closest("fieldset"),
  ).toHaveProperty("disabled", true);
  expect(post).not.toHaveBeenCalled();
});

it("keeps independent retry keys when multiple answers are drafted before saving", async () => {
  const user = userEvent.setup();
  const waiting = {
    run_id: run.id,
    status: "waiting",
    answers: [],
    successor: null,
  };
  post.mockResolvedValue({ data: waiting, response: new Response() });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        pending={{
          approvals: [approval("first"), approval("second")],
          calls: [],
        }}
      />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("button", { name: "Approve once" })[0]!
        .closest("fieldset"),
    ).toHaveProperty("disabled", false),
  );
  await user.click(screen.getAllByRole("button", { name: "Approve once" })[0]!);
  await user.click(screen.getAllByRole("button", { name: "Deny" })[1]!);
  await user.click(
    screen.getAllByRole("button", { name: "Save response" })[0]!,
  );
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("button", { name: "Save response" })[1]!
        .closest("fieldset"),
    ).toHaveProperty("disabled", false),
  );
  await user.click(
    screen.getAllByRole("button", { name: "Save response" })[1]!,
  );
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[0]![1].params.header).not.toEqual(
    post.mock.calls[1]![1].params.header,
  );
});
