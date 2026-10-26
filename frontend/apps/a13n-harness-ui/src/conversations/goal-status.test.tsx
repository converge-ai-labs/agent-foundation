// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { currentGoal, GoalStatus } from "./goal-status";
import type { Schema } from "../transport/client";

afterEach(cleanup);
// One row per rendering branch: active, the two statuses with their own
// guidance, verified completion, and an incomplete stop that offers retry.
it.each([
  ["working", "Working"],
  ["checking", "Checking"],
  ["auditing", "Reviewing"],
  ["suspended", "Needs input"],
  ["verified", "Verified"],
  ["max_iterations", "Not verified"],
  ["cancelled", "Not verified"],
  ["error", "Not verified"],
  ["unverified_stop", "Not verified"],
] as const)(
  "shows authoritative %s progress and explicit, non-submitting retry",
  async (status, label) => {
    const goal: Schema<"GoalView"> = {
      objective: "Check all requirements",
      iteration: 2,
      max_iterations: 10,
      status,
      input_tokens: 120,
      output_tokens: 30,
      needs_restore_audit: true,
    };
    const retry = vi.fn();
    render(<GoalStatus goal={goal} onUseObjective={retry} />);
    const button = screen.getByRole("button", { name: /Inspect Goal:/ });
    expect(screen.getByRole("status").textContent).toContain(label);
    expect(screen.getByRole("status").textContent?.includes("2")).toBe(
      status === "checking",
    );
    expect(screen.queryByText(goal.objective)).toBeNull();
    fireEvent.click(button);
    expect(await screen.findByText("Check all requirements")).toBeTruthy();
    expect(screen.getByText(/120 in/)).toBeTruthy();
    expect(screen.getByText("2 / 10")).toBeTruthy();
    if (status !== "auditing")
      expect(screen.getByText(/A fresh review is required/)).toBeTruthy();
    if (
      ["max_iterations", "cancelled", "error", "unverified_stop"].includes(
        status,
      )
    ) {
      expect(screen.getByRole("heading", { level: 3 }).textContent).toMatch(
        /not verified|before verification|without verification/,
      );
      expect(retry).not.toHaveBeenCalled();
      fireEvent.click(
        screen.getByRole("button", { name: "Use objective in composer" }),
      );
      expect(retry).toHaveBeenCalledTimes(1);
    } else
      expect(
        screen.queryByRole("button", { name: "Use objective in composer" }),
      ).toBeNull();
  },
);

it("uses saved Goal state when idle and only lets the matching live receipt override it", () => {
  const saved = {
    objective: "Saved goal",
    status: "suspended",
  } as Schema<"GoalView">;
  const live = {
    objective: "New goal",
    status: "checking",
  } as Schema<"GoalView">;
  const operation = {
    receipt: { receipt_id: "current" },
    goal: live,
  } as Schema<"RootOperationView">;
  const thread = { goal: saved, root_activity: { state: "inactive" } } as Pick<
    Schema<"ThreadSummary">,
    "goal" | "root_activity"
  >;
  expect(currentGoal(thread, operation)).toBe(saved);
  thread.goal = null;
  expect(currentGoal(thread, operation)).toBeNull();
  thread.root_activity = { state: "running", receipt_id: "current" };
  expect(currentGoal(thread, operation)).toBe(live);
  thread.goal = saved;
  expect(currentGoal(thread, { ...operation, goal: null })).toBeNull();
  expect(
    currentGoal(thread, {
      ...operation,
      receipt: { ...operation.receipt, receipt_id: "old" },
    }),
  ).toBe(saved);
});

it.each([0, -1])(
  "explains a disabled follow-up budget (%s) without negative counts",
  async (limit) => {
    render(
      <GoalStatus
        goal={{
          objective: "Check the task",
          status: "max_iterations",
          iteration: 0,
          max_iterations: limit,
        }}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("Not verified");
    fireEvent.click(screen.getByRole("button", { name: /Inspect Goal:/ }));
    expect(
      await screen.findByText("Automatic follow-up checks are disabled."),
    ).toBeTruthy();
    expect(screen.queryByText(/0 of/)).toBeNull();
  },
);

it("announces preparing separately from execution and keeps details open across phase updates", async () => {
  const goal = {
    objective: "Check the task",
    status: "working",
    iteration: 0,
    max_iterations: 10,
  } as Schema<"GoalView">;
  const view = render(
    <GoalStatus goal={goal} activity={{ state: "preparing" }} />,
  );
  expect(screen.getByRole("status").textContent).toContain("Preparing");
  fireEvent.click(screen.getByRole("button", { name: /Inspect Goal:/ }));
  await screen.findByText(goal.objective);
  view.rerender(
    <GoalStatus
      goal={{ ...goal, status: "checking", iteration: 1 }}
      activity={{ state: "running" }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("Checking 1");
  expect(screen.getByText(goal.objective)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Objective" }));
  expect(
    screen
      .getByRole("button", { name: "Objective" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  view.rerender(
    <GoalStatus
      goal={{ ...goal, status: "verified", iteration: 1 }}
      activity={{ state: "inactive" }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("Verified");
  expect(screen.getByRole("status").textContent).not.toContain("1");
  expect(
    screen.getByRole("heading", { name: "Verified by the Agent" }),
  ).toBeTruthy();
  expect(screen.getByText(/same-Agent verification/)).toBeTruthy();
});
