import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Menu, MenuPopup, MenuTrigger } from "a13n-ui";
import { expect, it, vi } from "vitest";
import { DuplicateAction } from "./duplicate-action";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("opens from the menu, requires a new model key, and keeps the draft on failure", async () => {
  const action = vi
    .fn()
    .mockRejectedValueOnce(new Error("Key already exists"))
    .mockResolvedValueOnce(undefined);
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Menu>
        <MenuTrigger>More actions</MenuTrigger>
        <MenuPopup>
          <DuplicateAction
            sourceName="Test model"
            title="Duplicate model"
            requiresKey
            action={action}
          />
        </MenuPopup>
      </Menu>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "More actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Duplicate" }));
  const dialog = within(
    await screen.findByRole("dialog", { name: "Duplicate model" }),
  );
  expect(
    dialog.getByRole<HTMLInputElement>("textbox", { name: "Display name" })
      .value,
  ).toBe("Test model (copy)");
  const submit = dialog.getByRole<HTMLButtonElement>("button", {
    name: "Duplicate",
  });
  const key = dialog.getByRole<HTMLInputElement>("textbox", {
    name: "Model key",
  });
  expect(submit.disabled).toBe(true);
  await user.type(key, "taken-key");
  await user.click(submit);
  await dialog.findByText("Key already exists");
  expect(action).toHaveBeenNthCalledWith(1, "Test model (copy)", "taken-key");
  expect(key.value).toBe("taken-key");
  await user.clear(key);
  await user.type(key, "new-key");
  await user.click(submit);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(action).toHaveBeenNthCalledWith(2, "Test model (copy)", "new-key");
});
