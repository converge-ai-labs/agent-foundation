import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuGroup,
  MenuItem,
  SettingsRow,
  SettingsSection,
  Switch,
  Tabs,
  TabsList,
  TabsTab,
  TabsPanel,
} from "../src";
it("action menus support keyboard navigation and skip disabled actions", async () => {
  const user = userEvent.setup();
  const select = vi.fn();
  render(
    <Menu>
      <MenuTrigger render={<Button>More</Button>} />
      <MenuPopup aria-label="Actions">
        <MenuGroup>
          <MenuItem disabled onClick={() => select("disabled")}>
            Unavailable
          </MenuItem>
          <MenuItem onClick={() => select("open")}>Open</MenuItem>
          <MenuItem onClick={() => select("rename")}>Rename</MenuItem>
        </MenuGroup>
      </MenuPopup>
    </Menu>,
  );
  await user.tab();
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(
    screen.getByRole("menuitem", { name: "Open" }),
  );
  await user.keyboard("{ArrowDown}{Enter}");
  expect(select).toHaveBeenCalledExactlyOnceWith("rename");
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "More" }),
    ),
  );
});
it("tabs navigate with arrow keys without entering the inactive panel", async () => {
  const user = userEvent.setup();
  render(
    <Tabs defaultValue="first">
      <TabsList aria-label="Views">
        <TabsTab value="first">First</TabsTab>
        <TabsTab value="second">Second</TabsTab>
      </TabsList>
      <TabsPanel value="first">
        <Button>First action</Button>
      </TabsPanel>
      <TabsPanel value="second">
        <Button>Second action</Button>
      </TabsPanel>
    </Tabs>,
  );
  await user.tab();
  await user.keyboard("{ArrowRight}{Enter}");
  expect(
    screen.getByRole("tab", { name: "Second" }).getAttribute("aria-selected"),
  ).toBe("true");
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "First action" })).toBeNull(),
  );
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
        <Switch id="theme" aria-describedby="theme-description" />
      </SettingsRow>
      <SettingsRow
        label="Notifications"
        controlId="notifications"
        description="Connect a channel first"
      >
        <Switch
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
  expect(disabled.getAttribute("aria-disabled")).toBe("true");
});
