// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Schema } from "../transport/client";
import { RunActivity } from "./run-activity";

afterEach(cleanup);

const accepted = {
  kind: "accepted",
  action: "send",
  receipt: "receipt-one",
} as const;
function operation(
  status: Schema<"RootOperationStatus">,
): Schema<"RootOperationView"> {
  return {
    receipt: {
      receipt_id: "receipt-one",
      thread_id: "thread-one",
      submitted_at: "2026-09-30T00:00:00Z",
    },
    status,
  };
}

it("shows immediate feedback through admission, preparation and execution before any output", () => {
  const view = render(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={{ kind: "pending", action: "send" }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Sending…");
  view.rerender(
    <RunActivity activity={{ state: "inactive" }} submission={accepted} />,
  );
  expect(screen.getByRole("status").textContent).toBe("Preparing…");
  view.rerender(
    <RunActivity
      activity={{ state: "preparing", receipt_id: "receipt-one" }}
      submission={accepted}
      operation={operation("preparing")}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Preparing…");
  view.rerender(
    <RunActivity
      activity={{ state: "running", receipt_id: "receipt-one" }}
      submission={accepted}
      operation={operation("running")}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Working…");
});

it("uses the accepted receipt while activity observations catch up", () => {
  const view = render(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={accepted}
      operation={{
        ...operation("completed"),
        receipt: { ...operation("completed").receipt, receipt_id: "previous" },
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Preparing…");
  view.rerender(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={accepted}
      operation={operation("running")}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Working…");
});

it.each(["completed", "suspended", "failed", "cancelled"] as const)(
  "dismisses feedback for a %s receipt even with retained accepted input",
  (status) => {
    const view = render(
      <RunActivity
        activity={{ state: "inactive" }}
        submission={accepted}
        operation={operation(status)}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
    view.rerender(
      <RunActivity
        activity={{ state: "running", receipt_id: "receipt-one" }}
        submission={accepted}
        operation={operation(status)}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
  },
);

it("does not invent execution for idle, rejected or unknown submissions", () => {
  const view = render(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={{ kind: "idle" }}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={{ kind: "rejected", message: "Rejected" }}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={{ kind: "unknown", action: "send", message: "Check status" }}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
});

it("distinguishes pending steering without starting another preparation indicator", () => {
  const view = render(
    <RunActivity
      activity={{ state: "running" }}
      submission={{ kind: "pending", action: "steer" }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("Sending instruction…");
  view.rerender(
    <RunActivity
      activity={{ state: "inactive" }}
      submission={{ ...accepted, action: "steer" }}
      operation={operation("completed")}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
});
