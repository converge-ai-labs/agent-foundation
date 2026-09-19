import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionTest } from "./connection-test";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("tests saved configuration inline and hides its result while edits are unsaved", async () => {
  const user = userEvent.setup();
  const action = vi
    .fn()
    .mockResolvedValue({ success: true, elapsed_ms: 12, message: "Connected" });
  const client = new QueryClient();
  const view = (dirty: boolean) => (
    <QueryClientProvider client={client}>
      <ConnectionTest
        action={action}
        description="Test saved model"
        dirty={dirty}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view(true));
  await user.click(screen.getByRole("button", { name: "Check connection" }));
  expect(action).not.toHaveBeenCalled();
  rerender(view(false));
  await user.click(screen.getByRole("button", { name: "Check connection" }));
  expect(await screen.findByText("Connected")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  rerender(view(true));
  expect(screen.queryByText("Connected")).toBeNull();
  expect(
    screen.getByText("Save your changes before checking the connection."),
  ).toBeTruthy();
});
