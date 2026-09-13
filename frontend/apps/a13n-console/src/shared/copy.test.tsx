import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "a13n-ui";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CopyableId } from "./copy";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
function setup() {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider closeLabel="Dismiss">
        <CopyableId value="ws_example_123" />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return user;
}
it("copies the complete identifier from the keyboard and announces success", async () => {
  const user = setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  screen.getByRole("button", { name: "Copy ID" }).focus();
  await user.keyboard("{Enter}");
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByRole("button", { name: "Copied" })).toBeDefined();
  expect(write).toHaveBeenCalledExactlyOnceWith("ws_example_123");
  expect(screen.getByRole("status").textContent).toBe("Copied");
  await act(() => vi.advanceTimersByTimeAsync(1500));
  expect(await screen.findByRole("button", { name: "Copy ID" })).toBeDefined();
  expect(screen.getByRole("status").textContent).toBe("");
});
it("shows a failed copy without reporting success and permits retry", async () => {
  const user = setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(
    new Error("Denied"),
  );
  await user.click(screen.getByRole("button", { name: "Copy ID" }));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
    "Copy failed.",
  );
  expect(screen.getByRole("status").textContent).toBe("");
  await user.click(screen.getByRole("button", { name: "Copy ID" }));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByRole("button", { name: "Copied" })).toBeDefined();
  await waitFor(() =>
    expect(document.querySelector('[role="alertdialog"]')).toBeNull(),
  );
});
