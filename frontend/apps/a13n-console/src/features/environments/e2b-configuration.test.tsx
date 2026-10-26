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
  "x-primary-fields": ["execution", "desktop", "template"],
  properties: {
    execution: {
      type: "string",
      enum: ["native", "envd_http"],
      default: "native",
      title: "Execution transport",
    },
    desktop: { type: "boolean", default: false },
    template: {
      type: "string",
      title: "E2B template",
      default: "base",
      minLength: 1,
    },
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

it("selects automatic desktop preparation and supports opting into a custom template", async () => {
  const user = userEvent.setup();
  render(<Editor />);
  expect(screen.getByText("base", { exact: true })).toBeTruthy();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    execution: "envd_http",
    desktop: true,
    template: "auto",
    desktop_assistance: true,
  });
  expect(
    (
      screen.getByRole("button", {
        name: "Automatic desktop",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  expect(screen.queryByRole("textbox", { name: "E2B template" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  await user.type(
    screen.getByRole("textbox", { name: "E2B template" }),
    "team-envd",
  );
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    execution: "envd_http",
    desktop: false,
    desktop_assistance: false,
    template: "team-envd",
  });
});

it("returns automatic desktop to the native base when desktop is disabled", async () => {
  const user = userEvent.setup();
  render(<Editor template="auto" desktop />);
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    template: "base",
    execution: "native",
    desktop: false,
    desktop_assistance: false,
  });
});

it("preserves an operator template when desktop is enabled", async () => {
  const user = userEvent.setup();
  render(<Editor template="team-envd" />);
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    execution: "envd_http",
    template: "team-envd",
  });
  expect(
    screen.getByText(/with a13n-envd and desktop components preinstalled/),
  ).toBeTruthy();
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
