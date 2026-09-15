// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "a13n-ui";
import { ConfirmAction } from "./confirm-action";

afterEach(cleanup);

function setup(confirmationRequired = true, disabled = false) {
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  render(
    <ConfirmAction
      trigger={<Button disabled={disabled}>Discard draft</Button>}
      title="Discard this draft?"
      description="Your unsaved input will be lost."
      confirmLabel="Discard draft"
      destructive
      confirmationRequired={confirmationRequired}
      onConfirm={onConfirm}
    />,
  );
  return { user, onConfirm };
}

it("requires explicit confirmation and focuses the non-destructive cancel action", async () => {
  const { user, onConfirm } = setup();
  const trigger = screen.getByRole("button", { name: "Discard draft" });
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", {
    name: "Discard this draft?",
  });
  expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
  const cancel = within(dialog).getByRole("button", { name: "Cancel" });
  await waitFor(() => expect(document.activeElement).toBe(cancel));
  expect(onConfirm).not.toHaveBeenCalled();
  await user.click(cancel);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(onConfirm).not.toHaveBeenCalled();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await user.click(trigger);
  await user.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Discard draft",
    }),
  );
  expect(onConfirm).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("preserves input when dismissed with Escape or the close control", async () => {
  const { user, onConfirm } = setup();
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  await user.click(
    await screen.findByRole("button", { name: "Close confirmation" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(onConfirm).not.toHaveBeenCalled();
});

it("keeps explicitly unguarded actions immediate", async () => {
  const { user, onConfirm } = setup(false);
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("preserves disabled triggers", async () => {
  const { user, onConfirm } = setup(true, true);
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});
