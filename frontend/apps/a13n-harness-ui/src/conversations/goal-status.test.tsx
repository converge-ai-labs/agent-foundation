// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { GoalStatus } from "./goal-status";
import type { Schema } from "../transport/client";

afterEach(cleanup);
// One row per rendering branch: active, the two statuses with their own
// guidance, verified completion, and an incomplete stop that offers retry.
it.each([
  ["working", "Working"],
  ["auditing", "Fresh audit"],
  ["suspended", "Waiting for response"],
  ["verified", "Agent-verified"],
  ["unverified_stop", "Unverified stop"],
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
    render(<GoalStatus goal={goal} onRetry={retry} />);
    const button = screen.getByRole("button", { name: "Goal details" });
    expect(button.textContent).toContain(label);
    expect(button.textContent).toContain("2/10");
    expect(button.textContent).toContain("Audit pending");
    fireEvent.click(button);
    expect(await screen.findByText("Check all requirements")).toBeTruthy();
    expect(screen.getByText(/120 input/)).toBeTruthy();
    if (
      ["max_iterations", "cancelled", "error", "unverified_stop"].includes(
        status,
      )
    ) {
      expect(screen.getByText(/task may be incomplete/)).toBeTruthy();
      expect(retry).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Prepare new Goal" }));
      expect(retry).toHaveBeenCalledTimes(1);
    } else
      expect(
        screen.queryByRole("button", { name: "Prepare new Goal" }),
      ).toBeNull();
  },
);
