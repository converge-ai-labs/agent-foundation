// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { LiveConnectionStatus } from "./live-connection";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("keeps short handshakes quiet and resets the delay after recovery", () => {
  vi.useFakeTimers();
  const view = render(
    <LiveConnectionStatus connection="Connecting" reconnections={0} />,
  );
  act(() => vi.advanceTimersByTime(800));
  view.rerender(
    <LiveConnectionStatus
      connection="Loading current output"
      reconnections={0}
    />,
  );
  act(() => vi.advanceTimersByTime(699));
  expect(screen.queryByRole("status")).toBeNull();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.getByRole("status").textContent).toBe("Loading current output");
  view.rerender(<LiveConnectionStatus connection="Live" reconnections={0} />);
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(
    <LiveConnectionStatus connection="Connecting" reconnections={0} />,
  );
  act(() => vi.advanceTimersByTime(1499));
  expect(screen.queryByRole("button")).toBeNull();
  view.rerender(<LiveConnectionStatus connection="Live" reconnections={0} />);
  act(() => vi.advanceTimersByTime(1500));
  expect(screen.queryByRole("status")).toBeNull();
});
it("discloses retries on click and removes status and details after recovery", async () => {
  vi.useFakeTimers();
  const view = render(
    <LiveConnectionStatus connection="Reconnecting" reconnections={1} />,
  );
  act(() => vi.advanceTimersByTime(1500));
  expect(screen.getByRole("status").textContent).toBe(
    "Reconnecting live updates…",
  );
  expect(screen.queryByText("1 retry")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Live updates connection" }),
  );
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(screen.getByText("1 retry")).toBeTruthy();
  view.rerender(
    <LiveConnectionStatus connection="Reconnecting" reconnections={2} />,
  );
  expect(screen.getByText("2 retries")).toBeTruthy();
  expect(screen.getByRole("status").textContent).not.toContain("retries");
  view.rerender(<LiveConnectionStatus connection="Live" reconnections={2} />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByText("2 retries")).toBeNull();
});
