import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { fixtureRun } from "./fixture";
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
const successor = fixtureRun({ id: "run_3", status: "accepted" });
/** The resumed Run the Service answers a resume with. */
function resumes() {
  post.mockResolvedValue({ data: successor, response: new Response() });
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

it("requires an explicit decision for every approval before sending the complete response set", async () => {
  const user = userEvent.setup();
  resumes();
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
    approvals: { first: { action: "approve" }, second: { action: "deny" } },
    calls: {},
  });
});

it("submits a bounded denial reason with the rest of the answers", async () => {
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
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
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
  expect(post.mock.calls[0]![0]).toBe("/api/v1/runs/{run_id}/resume");
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
    name: "Submit responses",
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
    name: "Submit responses",
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

it("answers questions and approvals together in a mixed wait", async () => {
  const user = userEvent.setup();
  resumes();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
        accepted={accepted}
        run={run}
        pending={{
          calls: [
            question({ questions: [questionPresentation.questions[0]!] }),
          ],
          approvals: [
            approval("approval", {
              risk: "low",
              reason: "Tool policy requires approval.",
            }),
          ],
        }}
      />
    </QueryClientProvider>,
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Approve once" }));
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body).toEqual({
    approvals: { approval: { action: "approve" } },
    calls: {
      question: {
        status: "returned",
        value: { answers: { "Which business?": "Retail" } },
      },
    },
  });
});

it("only skips a question after an explicit choice and submission", async () => {
  const user = renderQuestions();
  await user.click(
    screen.getByRole("button", { name: "Continue without a response" }),
  );
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
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
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("radio", { name: /Retail/ })).toHaveProperty(
    "checked",
    true,
  );
  expect(accepted).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
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
                presentation: {
                  title: "Review invoice",
                  description: "Check the total",
                },
              },
            ],
          }}
        />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Request details" }));
    expect(screen.getByText(/Check the total/)).toBeTruthy();
    await user.click(screen.getByRole("combobox", { name: "Response" }));
    await user.click(
      await screen.findByRole("option", {
        name: failed ? "Report tool failure" : "Return tool result",
      }),
    );
    if (failed) {
      await user.click(
        screen.getByRole("button", { name: "Submit responses" }),
      );
      expect((await screen.findByRole("alert")).textContent).toContain(
        "Enter a failure reason.",
      );
      expect(post).not.toHaveBeenCalled();
      await user.type(
        screen.getByRole("textbox", { name: "Failure reason" }),
        "Reviewer unavailable",
      );
    } else {
      await user.click(
        screen.getByRole("button", { name: "Submit responses" }),
      );
      await screen.findByRole("alert");
      expect(post).not.toHaveBeenCalled();
      await user.click(
        screen.getByRole("textbox", { name: "Response (JSON)" }),
      );
      await user.paste('{"approved":false}');
    }
    await user.click(screen.getByRole("button", { name: "Submit responses" }));
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
