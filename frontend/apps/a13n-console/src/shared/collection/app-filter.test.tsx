import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import "../../i18n";
import { Registry } from "../../app-plugins/context";
import type { InstalledApp } from "../../app-plugins/types";
import { AppBadge } from "../identity/app-badge";
import { AppFilter } from "./app-filter";

const app: InstalledApp = {
  app_id: "reports.headless",
  name: "Reports",
  distribution: "reports",
  version: "1",
  factory_keys: [],
  enabled: false,
  frontend: null,
};

it("resolves disabled headless Apps, keeps exact IDs inspectable, and supports a filter action", async () => {
  const onClick = vi.fn();
  render(
    <Registry.Provider
      value={{ apps: [], catalog: [app], pending: false, error: null }}
    >
      <AppBadge appId={app.app_id} onClick={onClick} />
      <AppBadge appId="uninstalled.app" />
    </Registry.Provider>,
  );
  const button = screen.getByRole("button", { name: "Filter by Reports" });
  expect(button.getAttribute("title")).toBe("Reports (reports.headless)");
  expect(within(button).getByText("Reports")).toBeTruthy();
  expect(
    screen
      .getByText("uninstalled.app")
      .closest("[title]")
      ?.getAttribute("title"),
  ).toBe("uninstalled.app");
  await userEvent.click(button);
  expect(onClick).toHaveBeenCalledOnce();
});

it("omits empty filter chrome, but keeps an unknown App selected and clearable", async () => {
  const onChange = vi.fn();
  const { rerender } = render(<AppFilter onChange={onChange} />);
  expect(screen.queryByRole("combobox")).toBeNull();
  rerender(<AppFilter value="uninstalled.app" onChange={onChange} />);
  const user = userEvent.setup();
  expect(screen.getByRole("combobox").textContent).toContain("uninstalled.app");
  await user.click(screen.getByRole("combobox"));
  await user.click(await screen.findByRole("option", { name: "All apps" }));
  expect(onChange).toHaveBeenCalledWith(undefined);
});

it("offers every installed App and emits its canonical ID", async () => {
  const onChange = vi.fn();
  render(
    <Registry.Provider
      value={{ apps: [], catalog: [app], pending: false, error: null }}
    >
      <AppFilter onChange={onChange} />
    </Registry.Provider>,
  );
  const user = userEvent.setup();
  const filter = screen.getByRole("combobox");
  expect(filter.textContent).toContain("All apps");
  await user.click(filter);
  await user.click(await screen.findByRole("option", { name: "Reports" }));
  expect(onChange).toHaveBeenCalledWith(app.app_id);
});
