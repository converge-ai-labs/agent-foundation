import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsRow, SettingsSection, Switch } from "../src";
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
