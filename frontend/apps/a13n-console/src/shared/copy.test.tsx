import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CopyableId } from "./copy";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function setup() {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CopyableId value="ws_example_123" />
    </QueryClientProvider>,
  );
  return user;
}
it("copies the complete identifier from the keyboard and announces success", async () => {
  const user = setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  screen.getByRole("button", { name: "Copy ID" }).focus();
  await user.keyboard("{Enter}");
  await screen.findByRole("button", { name: "Copied" });
  expect(write).toHaveBeenCalledExactlyOnceWith("ws_example_123");
  expect(screen.getByRole("status").textContent).toBe("Copied");
  await screen.findByRole("button", { name: "Copy ID" }, { timeout: 2500 });
  expect(screen.getByRole("status").textContent).toBe("");
});
it("shows a failed copy without reporting success and permits retry", async () => {
  const user = setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(
    new Error("Denied"),
  );
  await user.click(screen.getByRole("button", { name: "Copy ID" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("status").textContent).toBe("");
  await user.click(screen.getByRole("button", { name: "Copy ID" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(screen.queryByRole("alert")).toBeNull();
});
