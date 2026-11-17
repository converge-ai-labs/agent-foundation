// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
it("replaces the live failure with finalized diagnostics and does not reuse them for another receipt", () => {
  const display = new FocusDisplay();
  display.runId = "run-old";
  display.terminalFailure = "Stream failed";
  display.terminalFailureCode = "agent_run_failed";
  const operation = {
    receipt: { receipt_id: "old" },
    status: "running",
    run_id: "run-old",
    failure: {
      code: "checkpoint_save_failed",
      message: "Saved failure\nDiagnostic report: /tmp/report.json",
    },
  } as Schema<"RootOperationView">;
  vi.mocked(useOperation).mockReturnValue({ data: operation } as ReturnType<
    typeof useOperation
  >);
  const view = render(
    <RootFailureNotice threadId="thread" receipt="old" display={display} />,
  );
  expect(screen.getByText("Stream failed")).toBeTruthy();
  expect(screen.getByText("Error code: agent_run_failed")).toBeTruthy();
  operation.status = "failed";
  view.rerender(
    <RootFailureNotice threadId="thread" receipt="old" display={display} />,
  );
  expect(screen.getByText("Saved failure")).toBeTruthy();
  expect(screen.queryByText("Stream failed")).toBeNull();
  expect(screen.getByText("Error code: checkpoint_save_failed")).toBeTruthy();
  fireEvent.click(screen.getByText("Error details"));
  expect(
    screen.getByText(/Diagnostic report: \/tmp\/report.json/).textContent,
  ).toContain("Run ID: run-old");
  expect(
    screen.getByText(/Diagnostic report: \/tmp\/report.json/).textContent,
  ).toContain("Execution ID: old");
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

it("offers continuation only for confirmed failure and disables unresolved submission", () => {
  const retry = vi.fn();
  const display = new FocusDisplay();
  const operation = {
    receipt: { receipt_id: "failed" },
    status: "failed",
    run_id: "run-failed",
    failure: { message: "Provider failed" },
  } as Schema<"RootOperationView">;
  vi.mocked(useOperation).mockReturnValue({ data: operation } as ReturnType<
    typeof useOperation
  >);
  const view = render(
    <RootFailureNotice
      threadId="thread"
      receipt="failed"
      display={display}
      retry={retry}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledTimes(1);
  view.rerender(
    <RootFailureNotice
      threadId="thread"
      receipt="failed"
      display={display}
      retry={retry}
      retryDisabled
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledTimes(1);
  vi.mocked(useOperation).mockReturnValue({
    data: { ...operation, status: "running" },
  } as ReturnType<typeof useOperation>);
  display.runId = "run-failed";
  display.terminalFailure = "Observed live failure, awaiting receipt";
  view.rerender(
    <RootFailureNotice
      threadId="thread"
      receipt="failed"
      display={display}
      retry={retry}
    />,
  );
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

it.each(["execution", "continuation"] as const)(
  "shows the finalized %s failure instead of the generic stream error",
  (source) => {
    const display = new FocusDisplay();
    display.runId = "run-failed";
    display.terminalFailure = "Agent execution failed.";
    display.terminalFailureCode = "agent_run_failed";
    vi.mocked(useOperation).mockReturnValue({
      data: {
        receipt: { receipt_id: "failed" },
        status: "failed",
        run_id: "run-failed",
        outcome: {
          execution: {},
          continuation: {},
          [source]: {
            failure: {
              code: "checkpoint_save_failed",
              message:
                "No space left on device (ENOSPC, errno 28).\nDiagnostic report: /tmp/report.json",
              details: { errno: 28 },
            },
          },
        },
      },
    } as ReturnType<typeof useOperation>);
    render(
      <RootFailureNotice
        threadId="thread"
        receipt="failed"
        display={display}
      />,
    );
    expect(screen.queryByText("Agent execution failed.")).toBeNull();
    expect(
      screen.getByText("No space left on device (ENOSPC, errno 28)."),
    ).toBeTruthy();
    expect(screen.getByText("Error code: checkpoint_save_failed")).toBeTruthy();
    fireEvent.click(screen.getByText("Error details"));
    expect(
      screen.getByText(/Diagnostic report: \/tmp\/report.json/).textContent,
    ).toContain('"errno": 28');
  },
);

it.each([true, false])(
  "retains failure details but removes retry after clearing context (checkpoint: %s)",
  (checkpoint) => {
    const operation = {
      receipt: { receipt_id: "failed" },
      status: "failed",
      failure: { message: "Previous provider failure" },
      outcome: checkpoint
        ? { continuation: { status: "selected", continuation_id: "saved" } }
        : undefined,
    } as Schema<"RootOperationView">;
    vi.mocked(useOperation).mockReturnValue({ data: operation } as ReturnType<
      typeof useOperation
    >);
    const display = new FocusDisplay();
    const retry = vi.fn();
    const content = (continuationId: string) => (
      <RootFailureNotice
        threadId="thread"
        receipt="failed"
        display={display}
        continuationId={continuationId}
        completedContinuationId={checkpoint ? "previous" : "saved"}
        retry={retry}
      />
    );
    const view = render(content("saved"));
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    view.rerender(content("cleared"));
    expect(screen.getByText("Previous provider failure")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(retry).not.toHaveBeenCalled();
  },
);

it("restores a durable failure after cold reload without fetching an expired receipt", () => {
  vi.mocked(useOperation).mockReturnValue(
    {} as ReturnType<typeof useOperation>,
  );
  const display = new FocusDisplay();
  const lastExecution: Schema<"ThreadExecution"> = {
    execution_id: "receipt-previous-process",
    status: "failed",
    submitted_at: "2026-01-01T00:00:00Z",
    error_code: "preparation_failed",
    error_message:
      "Environment preparation failed\nRepair its configuration before continuing.",
  };
  const view = render(
    <RootFailureNotice
      threadId="thread"
      display={display}
      lastExecution={lastExecution}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "Environment preparation failed",
  );
  expect(screen.getByText("Error code: preparation_failed")).toBeTruthy();
  expect(
    screen.getByText(/Execution ID: receipt-previous-process/),
  ).toBeTruthy();
  expect(screen.getByText("Error details").closest("details")?.open).toBe(
    false,
  );
  expect(vi.mocked(useOperation).mock.lastCall?.[1]).toBeUndefined();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  vi.mocked(useOperation).mockReturnValue({
    data: {
      receipt: {
        receipt_id: "receipt-new",
        submitted_at: "2026-01-02T00:00:00Z",
      },
      status: "preparing",
    },
  } as ReturnType<typeof useOperation>);
  view.rerender(
    <RootFailureNotice
      threadId="thread"
      receipt="receipt-new"
      display={display}
      lastExecution={lastExecution}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
});

it("distinguishes an unknown outcome from confirmed failure without offering automatic retry", () => {
  vi.mocked(useOperation).mockReturnValue(
    {} as ReturnType<typeof useOperation>,
  );
  render(
    <RootFailureNotice
      threadId="thread"
      display={new FocusDisplay()}
      lastExecution={{
        execution_id: "receipt-lost",
        status: "unknown",
        submitted_at: "2026-01-01T00:00:00Z",
        error_message: "No terminal outcome was saved.",
      }}
      retry={vi.fn()}
    />,
  );
  expect(screen.getByText("Execution outcome unknown")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});

it.each(["failed", "unknown"] as const)(
  "shows newer durable %s despite a cached receipt",
  (status) => {
    vi.mocked(useOperation).mockReturnValue({
      data: {
        receipt: { receipt_id: "old", submitted_at: "2026-01-01T00:00:00Z" },
        status: "failed",
        failure: { message: "Old failure" },
      },
    } as ReturnType<typeof useOperation>);
    render(
      <RootFailureNotice
        threadId="thread"
        receipt="old"
        display={new FocusDisplay()}
        lastExecution={{
          execution_id: "new",
          submitted_at: "2026-01-02T00:00:00Z",
          status,
          error_message: "New outcome",
        }}
        retry={vi.fn()}
      />,
    );
    expect(screen.getByText("New outcome")).toBeTruthy();
    expect(screen.queryByText("Old failure")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  },
);
