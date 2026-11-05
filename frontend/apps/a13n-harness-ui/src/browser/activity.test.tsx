// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BrowserActivity } from "./activity";
const live = vi.hoisted(() => ({
  display: { browserActivityRunId: undefined as string | undefined },
}));
vi.mock("../conversations/live-threads", () => ({ useLiveThread: () => live }));
afterEach(() => {
  cleanup();
  localStorage.clear();
  live.display.browserActivityRunId = undefined;
  vi.unstubAllGlobals();
});
function setup({ occupied = false, narrow = false, threadId = "thread" } = {}) {
  vi.stubGlobal("matchMedia", () => ({ matches: narrow }));
  const reveal = vi.fn();
  const open = vi.fn();
  const view = (visible = false, busy = occupied) => (
    <BrowserActivity
      threadId={threadId}
      occupied={busy}
      visible={visible}
      reveal={reveal}
      open={open}
    />
  );
  const mounted = render(view());
  return {
    reveal,
    open,
    update: (visible = false, busy = occupied) =>
      mounted.rerender(view(visible, busy)),
  };
}
it("reveals new foreground activity once without replaying retained activity", () => {
  live.display.browserActivityRunId = "old";
  const view = setup();
  expect(view.reveal).not.toHaveBeenCalled();
  live.display.browserActivityRunId = "new";
  view.update();
  expect(view.reveal).toHaveBeenCalledTimes(1);
  view.update(); // Closing the viewer does not change the live signal.
  expect(view.reveal).toHaveBeenCalledTimes(1);
});
it("does not reveal again in later Runs or after remounting the same Thread", () => {
  const first = setup();
  live.display.browserActivityRunId = "run-one";
  first.update();
  expect(first.reveal).toHaveBeenCalledOnce();
  live.display.browserActivityRunId = "run-two";
  first.update();
  expect(first.reveal).toHaveBeenCalledOnce();
  cleanup();
  const returned = setup();
  live.display.browserActivityRunId = "run-three";
  returned.update();
  expect(returned.reveal).not.toHaveBeenCalled();
  cleanup();
  const other = setup({ threadId: "other" });
  live.display.browserActivityRunId = "other-run";
  other.update();
  expect(other.reveal).toHaveBeenCalledOnce();
});

it("remembers a pending suggestion satisfied through the ordinary Browser entry", () => {
  const view = setup({ occupied: true });
  live.display.browserActivityRunId = "run-one";
  view.update();
  expect(screen.getByRole("button", { name: "View browser" })).toBeTruthy();
  view.update(true);
  expect(screen.queryByRole("button")).toBeNull();
  view.update(false, false);
  live.display.browserActivityRunId = "run-two";
  view.update(false, false);
  expect(view.reveal).not.toHaveBeenCalled();
  expect(screen.queryByRole("button")).toBeNull();
});

it.each([{ occupied: true }, { narrow: true }])(
  "offers a prompt instead of replacing the user's view: %o",
  (options) => {
    const view = setup(options);
    live.display.browserActivityRunId = "run";
    view.update();
    expect(view.reveal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View browser" }));
    expect(view.open).toHaveBeenCalledOnce();
    live.display.browserActivityRunId = "next-run";
    view.update();
    expect(view.reveal).not.toHaveBeenCalled();
    expect(screen.queryByRole("button")).toBeNull();
  },
);
