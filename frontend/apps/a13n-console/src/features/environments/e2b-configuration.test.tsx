import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProviderConfiguration } from "./provider-configuration";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, args?: { template?: string }) =>
      key.replace("{{template}}", args?.template ?? ""),
  }),
}));
const schema = {
  type: "object",
  "x-primary-fields": ["desktop", "template"],
  properties: {
    desktop: { type: "boolean", default: false },
    template: {
      type: "string",
      title: "E2B template",
      default: "base",
      minLength: 1,
    },
    desktop_width: { type: "integer", title: "Desktop width", default: 1280 },
    desktop_height: { type: "integer", title: "Desktop height", default: 800 },
    desktop_dpi: { type: "integer", title: "Desktop DPI", default: 96 },
    desktop_display: { type: "string", title: "X11 display", default: ":0" },
    allow_internet_access: { type: "boolean", default: true },
    root: { type: "string", title: "Root", default: "/home/user" },
  },
};
function Editor({
  template = "base",
  desktop = false,
}: {
  template?: string;
  desktop?: boolean;
}) {
  const [text, setText] = useState(JSON.stringify({ template, desktop }));
  return (
    <>
      <ProviderConfiguration
        variant="e2b"
        schema={schema}
        text={text}
        onChange={setText}
      />
      <output data-testid="config">{text}</output>
    </>
  );
}
function configuration() {
  return JSON.parse(screen.getByTestId("config").textContent ?? "{}");
}

it("uses official defaults and only shows desktop settings when enabled, preserving edits", async () => {
  const user = userEvent.setup();
  render(<Editor />);
  expect(screen.getByText("base", { exact: true })).toBeTruthy();
  await user.click(
    screen.getByRole("switch", { name: "Allow internet access" }),
  );
  expect(configuration().allow_internet_access).toBe(false);
  expect(screen.queryByRole("textbox", { name: "E2B template" })).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Advanced environment configuration" }),
  );
  expect(screen.getByRole("textbox", { name: "Root" })).toBeTruthy();
  expect(
    screen.queryByRole("spinbutton", { name: "Desktop width" }),
  ).toBeNull();
  expect(screen.queryByRole("textbox", { name: "X11 display" })).toBeNull();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(screen.getByText("desktop", { exact: true })).toBeTruthy();
  expect(configuration().template).toBe("desktop");
  const width = screen.getByRole("spinbutton", { name: "Desktop width" });
  await user.clear(width);
  await user.type(width, "1600");
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: false,
    template: "base",
    desktop_width: 1600,
  });
  expect(
    screen.queryByRole("spinbutton", { name: "Desktop width" }),
  ).toBeNull();
  expect(
    screen.queryByRole("spinbutton", { name: "Desktop height" }),
  ).toBeNull();
  expect(screen.queryByRole("spinbutton", { name: "Desktop DPI" })).toBeNull();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Desktop width",
      }) as HTMLInputElement
    ).value,
  ).toBe("1600");
});

it("preserves a custom template across desktop and official/custom mode changes", async () => {
  const user = userEvent.setup();
  render(<Editor template="team-desktop" />);
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(
    (screen.getByRole("textbox", { name: "E2B template" }) as HTMLInputElement)
      .value,
  ).toBe("team-desktop");
  expect(screen.getByText(/with desktop components preinstalled/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Official default" }));
  expect(configuration().template).toBe("desktop");
  expect(screen.queryByRole("textbox", { name: "E2B template" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  expect(configuration().template).toBe("team-desktop");
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: false,
    template: "team-desktop",
  });
});

it("permits custom templates in ordinary mode and resets the custom selection", async () => {
  const user = userEvent.setup();
  render(<Editor />);
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  await user.type(
    screen.getByRole("textbox", { name: "E2B template" }),
    "team-python",
  );
  expect(configuration()).toMatchObject({
    desktop: false,
    template: "team-python",
  });
  await user.click(screen.getByRole("button", { name: "Reset to defaults" }));
  expect(screen.queryByRole("textbox", { name: "E2B template" })).toBeNull();
  expect(screen.getByText("base", { exact: true })).toBeTruthy();
});
