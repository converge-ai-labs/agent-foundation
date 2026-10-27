import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  Button,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "../src";

it("retains content when the caller clears it during exit and uses fresh content on reopen", async () => {
  const finished = new Promise<Animation>(() => {});
  const animations = vi
    .spyOn(Element.prototype, "getAnimations")
    .mockReturnValue([{ finished, playState: "running" } as Animation]);
  const view = (open: boolean, content: string) => (
    <ModalFrame
      open={open}
      title="Provider"
      closeLabel="Close"
      trigger={<Button>Open</Button>}
    >
      {open && <p>{content}</p>}
    </ModalFrame>
  );
  try {
    const { rerender } = render(view(true, "Provider configuration"));
    await screen.findByText("Provider configuration");
    rerender(view(false, ""));
    expect(screen.getByText("Provider configuration")).toBeTruthy();
    rerender(view(true, "New configuration"));
    await waitFor(() =>
      expect(screen.queryByText("Provider configuration")).toBeNull(),
    );
    expect(screen.getByText("New configuration")).toBeTruthy();
  } finally {
    animations.mockRestore();
  }
});

it("supports stable top placement without changing the centered default", () => {
  const { rerender } = render(
    <ModalFrame open title="Connection" closeLabel="Close" placement="top">
      <p>Authorization</p>
    </ModalFrame>,
  );
  expect(screen.getByRole("dialog").getAttribute("data-placement")).toBe("top");

  rerender(
    <ModalFrame open title="Connection" closeLabel="Close">
      <p>Authorization</p>
    </ModalFrame>,
  );
  expect(screen.getByRole("dialog").getAttribute("data-placement")).toBe(
    "center",
  );
});

it("dismisses edited forms on outside press without submitting", async () => {
  const user = userEvent.setup();
  const submit = vi.fn((event: React.FormEvent) => event.preventDefault());
  render(
    <ModalFrame
      title="Edit resource"
      closeLabel="Close"
      trigger={<Button>Edit</Button>}
    >
      <form onSubmit={submit}>
        <input aria-label="Name" />
        <Button type="submit">Save</Button>
      </form>
    </ModalFrame>,
  );
  const trigger = screen.getByRole("button", { name: "Edit" });
  await user.click(trigger);
  await user.type(screen.getByRole("textbox", { name: "Name" }), "Draft");
  await user.click(document.querySelector('[data-slot="dialog-viewport"]')!);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(submit).not.toHaveBeenCalled();
});

it.each(["outside", "escape"])(
  "%s dismisses only the topmost nested modal",
  async (dismiss) => {
    const user = userEvent.setup();
    render(
      <ModalFrame title="Parent" closeLabel="Close" defaultOpen>
        <ModalFrame
          title="Child"
          closeLabel="Close"
          trigger={<Button>Inspect</Button>}
        >
          <p>Child content</p>
        </ModalFrame>
      </ModalFrame>,
    );
    await user.click(screen.getByRole("button", { name: "Inspect" }));
    if (dismiss === "escape") {
      await user.keyboard("{Escape}");
    } else {
      const viewports = document.querySelectorAll(
        '[data-slot="dialog-viewport"]',
      );
      await user.click(viewports[viewports.length - 1]);
    }
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Child" })).toBeNull(),
    );
    expect(screen.getByRole("dialog", { name: "Parent" })).toBeTruthy();
  },
);

it("lets a dialog opened from a menu own typing and Escape", async () => {
  const user = userEvent.setup();
  render(
    <Menu>
      <MenuTrigger render={<Button />}>Actions</MenuTrigger>
      <MenuPopup>
        <ModalFrame
          title="Create key"
          closeLabel="Close"
          trigger={
            <MenuItem
              nativeButton
              render={<button type="button" />}
              closeOnClick={false}
            >
              Create key
            </MenuItem>
          }
        >
          <input aria-label="Name" />
        </ModalFrame>
      </MenuPopup>
    </Menu>,
  );
  await user.tab();
  await user.keyboard("{ArrowDown}");
  await user.click(await screen.findByRole("menuitem", { name: "Create key" }));
  const name = screen.getByRole("textbox", { name: "Name" });
  await user.type(name, "Release app");
  expect((name as HTMLInputElement).value).toBe("Release app");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
