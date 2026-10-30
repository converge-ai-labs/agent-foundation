// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { copyText } from "../platform";
import { DebugData, DebugId, DebugProvider } from "./debug";

vi.mock("../platform", () => ({
  copyText: vi.fn(),
  modifierLabel: () => "Ctrl",
  usesCommandKey: () => false,
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.debug;
  vi.resetAllMocks();
});

it.each(["identifier", "data"])(
  "recovers from a denied %s copy without losing the original value",
  async (kind) => {
    localStorage.setItem("a13n-harness-ui.debug", "on");
    vi.mocked(copyText)
      .mockRejectedValueOnce(new Error("Clipboard denied"))
      .mockResolvedValueOnce(undefined);
    render(
      <DebugProvider>
        {kind === "identifier" ? (
          <DebugId label="Run" value="run-example" />
        ) : (
          <DebugData value={{ run: "run-example" }} open />
        )}
      </DebugProvider>,
    );
    const button = screen.getByRole("button");
    fireEvent.click(button);
    await screen.findByRole("alert");
    expect(screen.getByText(/run-example/)).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    await screen.findByText("Copied");
    expect(copyText).toHaveBeenCalledTimes(2);
  },
);
