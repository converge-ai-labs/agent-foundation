// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { RootFailureNotice, FailureNotice } from "./failure-notice";
import { FocusDisplay } from "./stream";
import { useOperation } from "./queries";
import type { Schema } from "../transport/client";
vi.mock("./queries", () => ({ useOperation: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("shows a concise failure with longer details collapsed and no retry action", () => {
  const message = "Provider rejected the request\nDetails remain inspectable.";
  render(<FailureNotice message={message} />);
  expect(screen.getByRole("alert").textContent).toContain("Provider rejected");
  expect(screen.getByText("Error details").closest("details")?.open).toBe(
    false,
  );
  expect(screen.queryByRole("button")).toBeNull();
});
it("shows current terminal failure after saved cutover and does not reuse it for another receipt", () => {
  const display = new FocusDisplay();
  display.runId = "run-old";
  display.terminalFailure = "Stream failed";
  const operation = {
    receipt: { receipt_id: "old" },
    status: "failed",
    run_id: "run-old",
    failure: { message: "Saved failure" },
  } as Schema<"RootOperationView">;
  vi.mocked(useOperation).mockReturnValue({ data: operation } as ReturnType<
    typeof useOperation
  >);
  const view = render(
    <RootFailureNotice threadId="thread" receipt="old" display={display} />,
  );
  expect(screen.getByText("Stream failed")).toBeTruthy();
  display.terminalFailure = undefined;
  view.rerender(
    <RootFailureNotice threadId="thread" receipt="old" display={display} />,
  );
  expect(screen.getByText("Saved failure")).toBeTruthy();
  vi.mocked(useOperation).mockReturnValue({
    data: {
      ...operation,
      receipt: { receipt_id: "new" },
      run_id: "run-new",
      status: "running",
    },
  } as ReturnType<typeof useOperation>);
  display.terminalFailure = "Stream failed";
  view.rerender(
    <RootFailureNotice threadId="thread" receipt="new" display={display} />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
});
