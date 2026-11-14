import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useEffect } from "react";
import { expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "../src/components/toast";

function Burst({ count, onAction }: { count: number; onAction: () => void }) {
  const { add } = useToast();
  useEffect(() => {
    for (let index = 0; index < count; index++) {
      add({
        id: `notice-${index}`,
        title: `Notice ${index}`,
        description: "An actual result preview",
        timeout: 0,
        actionProps: { children: "Open chat", onClick: onAction },
      });
    }
  }, [count, onAction, add]);
  return null;
}

it("bounds a persistent burst to the newest three notices and keeps actions named", async () => {
  const onAction = vi.fn();
  render(
    <ToastProvider closeLabel="Dismiss notification">
      <Burst count={40} onAction={onAction} />
    </ToastProvider>,
  );
  await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(3));
  expect(screen.queryByText("Notice 0")).toBeNull();
  const latest = screen.getByRole("dialog", { name: "Notice 39" });
  fireEvent.click(within(latest).getByRole("button", { name: "Open chat" }));
  expect(onAction).toHaveBeenCalledOnce();
  fireEvent.click(
    within(latest).getByRole("button", { name: "Dismiss notification" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Notice 39" })).toBeNull(),
  );
  // Expelled older notices never resurface after dismissing the front card.
  expect(screen.getAllByRole("dialog")).toHaveLength(2);
});

it("retains an input-needed notice until explicitly dismissed", async () => {
  vi.useFakeTimers();
  const onAction = vi.fn();
  render(
    <ToastProvider closeLabel="Dismiss notification">
      <Burst count={1} onAction={onAction} />
    </ToastProvider>,
  );
  try {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(screen.getByRole("dialog", { name: "Notice 0" })).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});
