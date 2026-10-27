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
  live.display.browserActivityRunId = undefined;
  vi.unstubAllGlobals();
});
function setup({ occupied = false, narrow = false } = {}) {
  vi.stubGlobal("matchMedia", () => ({ matches: narrow }));
  const reveal = vi.fn();
  const open = vi.fn();
  const view = () => (
    <BrowserActivity
      threadId="thread"
      occupied={occupied}
      visible={false}
      reveal={reveal}
      open={open}
    />
  );
  const mounted = render(view());
  return { reveal, open, update: () => mounted.rerender(view()) };
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
it.each([{ occupied: true }, { narrow: true }])(
  "offers a prompt instead of replacing the user's view: %o",
  (options) => {
    const view = setup(options);
    live.display.browserActivityRunId = "run";
    view.update();
    expect(view.reveal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View browser" }));
    expect(view.open).toHaveBeenCalledOnce();
    view.update();
    expect(screen.queryByRole("button")).toBeNull();
  },
);
