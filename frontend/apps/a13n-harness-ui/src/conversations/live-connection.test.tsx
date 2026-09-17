// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { LiveConnectionNotice } from "./live-connection";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("keeps short replay handshakes quiet and reveals sustained loading", () => {
  vi.useFakeTimers();
  const view = render(
    <LiveConnectionNotice connection="Connecting" reconnections={0} />,
  );
  view.rerender(
    <LiveConnectionNotice
      connection="Loading current output"
      reconnections={0}
    />,
  );
  act(() => vi.advanceTimersByTime(500));
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(<LiveConnectionNotice connection="Live" reconnections={0} />);
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(
    <LiveConnectionNotice
      connection="Loading current output"
      reconnections={0}
    />,
  );
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByRole("status").textContent).toBe("Loading current output");
});
it("retains the same notice through recovery rather than adding and removing layout rows", () => {
  const view = render(
    <LiveConnectionNotice connection="Reconnecting" reconnections={1} />,
  );
  const notice = screen.getByRole("status");
  view.rerender(<LiveConnectionNotice connection="Live" reconnections={1} />);
  expect(screen.getByRole("status")).toBe(notice);
  expect(notice.textContent).toBe("Live connection restored · 1 retry");
});
