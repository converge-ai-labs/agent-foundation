import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { PluginSettings } from "./plugin-settings";
import type { PluginType } from "../plugins";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
const definition: PluginType = {
  plugin_key: "test.audit",
  display_name: "Audit",
  description: "Record runs",
  distribution_name: null,
  distribution_version: null,
  config_schema: {
    type: "object",
    properties: {
      enabled: { type: "boolean", title: "Record events", default: true },
    },
  },
};
const initial = {
  instance_name: "audit",
  plugin_key: "test.audit",
  config: { enabled: false, future: { retained: true } },
};

it("preserves unknown values across form and JSON edits, rejects duplicates, and applies locally", async () => {
  const user = userEvent.setup(),
    onApply = vi.fn(),
    onClose = vi.fn();
  render(
    <PluginSettings
      initial={initial}
      plugins={[initial, { ...initial, instance_name: "duplicate" }]}
      catalog={[definition]}
      readOnly={false}
      control={{ controlledOpen: true, onClose }}
      onApply={onApply}
    />,
  );
  await user.click(screen.getByRole("switch", { name: "Record events" }));
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    JSON.parse(
      (
        screen.getByLabelText(
          "Plugin configuration (JSON)",
        ) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual({ enabled: true, future: { retained: true } });
  const name = screen.getByLabelText("Instance name");
  await user.clear(name);
  await user.type(name, "duplicate");
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).not.toHaveBeenCalled();
  expect(
    screen.getByText(
      "This agent already has a plugin with this instance name.",
    ),
  ).toBeTruthy();
  await user.clear(name);
  await user.type(name, "renamed");
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).toHaveBeenCalledWith({
    ...initial,
    instance_name: "renamed",
    config: { enabled: true, future: { retained: true } },
  });
  expect(onClose).toHaveBeenCalledOnce();
});

it("keeps schema-less JSON, rejects non-objects, and discards canceled edits", async () => {
  const user = userEvent.setup(),
    onApply = vi.fn(),
    onClose = vi.fn();
  render(
    <PluginSettings
      initial={initial}
      plugins={[initial]}
      catalog={[{ ...definition, config_schema: null }]}
      readOnly={false}
      control={{ controlledOpen: true, onClose }}
      onApply={onApply}
    />,
  );
  const input = screen.getByLabelText("Plugin configuration (JSON)");
  await user.clear(input);
  await user.paste("[]");
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(screen.getByText("Enter a JSON object.")).toBeTruthy();
  expect(onApply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onClose).toHaveBeenCalledOnce();
  expect(initial.config).toEqual({
    enabled: false,
    future: { retained: true },
  });
});

it("keeps unavailable and preset configuration readable without an apply action", () => {
  render(
    <PluginSettings
      initial={initial}
      plugins={[initial]}
      catalog={[]}
      readOnly
      control={{ controlledOpen: true, onClose: vi.fn() }}
      onApply={vi.fn()}
    />,
  );
  expect(
    screen.getByLabelText("Plugin configuration (JSON)").textContent,
  ).toContain("retained");
  expect(screen.getByLabelText("Instance name").textContent).toContain("audit");
  expect(screen.queryByRole("textbox", { name: "Instance name" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
});
