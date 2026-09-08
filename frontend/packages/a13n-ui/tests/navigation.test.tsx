import { useState } from "react";
import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Button,
  CommandPalette,
  Menu,
  SettingsRow,
  SettingsSection,
  Switch,
  Tabs,
} from "../src";
it("opens command search with focus, selects once, and closes", async () => {
  const user = userEvent.setup();
  const select = vi.fn();
  function Example() {
    const [open, setOpen] = useState(false);
    return (
      <CommandPalette
        trigger={<Button>Commands</Button>}
        open={open}
        onOpenChange={setOpen}
        label="Navigation"
        closeLabel="Close"
        placeholder="Search commands"
        emptyMessage="Nothing found"
        groups={[
          {
            label: "Pages",
            options: [{ value: "settings", label: "Settings" }],
          },
        ]}
        onSelect={select}
      />
    );
  }
  render(<Example />);
  const trigger = screen.getByRole("button", { name: "Commands" });
  await user.click(trigger);
  expect(document.activeElement).toBe(
    screen.getByRole("combobox", { name: "Navigation" }),
  );
  await user.keyboard("{Enter}");
  expect(select).toHaveBeenCalledExactlyOnceWith("settings");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
it("action menus support keyboard navigation and skip disabled actions", async () => {
  const user = userEvent.setup();
  const select = vi.fn();
  render(
    <Menu
      label="Actions"
      trigger={<Button>More</Button>}
      groups={[
        {
          actions: [
            {
              id: "disabled",
              label: "Unavailable",
              disabled: true,
              onSelect: () => select("disabled"),
            },
            { id: "open", label: "Open", onSelect: () => select("open") },
            { id: "rename", label: "Rename", onSelect: () => select("rename") },
          ],
        },
      ]}
    />,
  );
  await user.tab();
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(
    screen.getByRole("menuitem", { name: "Open" }),
  );
  await user.keyboard("{ArrowDown}{Enter}");
  expect(select).toHaveBeenCalledExactlyOnceWith("rename");
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "More" }),
  );
});
it("tabs navigate with arrow keys without entering the inactive panel", async () => {
  const user = userEvent.setup();
  render(
    <Tabs
      label="Views"
      defaultValue="first"
      items={[
        {
          value: "first",
          label: "First",
          content: <Button>First action</Button>,
        },
        {
          value: "second",
          label: "Second",
          content: <Button>Second action</Button>,
        },
      ]}
    />,
  );
  await user.tab();
  await user.keyboard("{ArrowRight}");
  expect(
    screen.getByRole("tab", { name: "Second" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.queryByRole("button", { name: "First action" })).toBeNull();
  await user.tab();
  await user.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Second action" }),
  );
});
it("setting labels activate their control and descriptions explain disabled states", async () => {
  const user = userEvent.setup();
  render(
    <SettingsSection title="Appearance">
      <SettingsRow
        label="Dark theme"
        controlId="theme"
        description="For low light"
      >
        <Switch
          label="Dark theme"
          labelHidden
          id="theme"
          aria-describedby="theme-description"
        />
      </SettingsRow>
      <SettingsRow
        label="Notifications"
        controlId="notifications"
        description="Connect a channel first"
      >
        <Switch
          label="Notifications"
          labelHidden
          id="notifications"
          aria-describedby="notifications-description"
          disabled
        />
      </SettingsRow>
    </SettingsSection>,
  );
  await user.click(screen.getAllByText("Dark theme")[0]!);
  expect(
    screen
      .getByRole("switch", { name: "Dark theme" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  const disabled = screen.getByRole("switch", { name: "Notifications" });
  expect(
    document.getElementById(disabled.getAttribute("aria-describedby")!)
      ?.textContent,
  ).toBe("Connect a channel first");
  expect(disabled.hasAttribute("disabled")).toBe(true);
});
