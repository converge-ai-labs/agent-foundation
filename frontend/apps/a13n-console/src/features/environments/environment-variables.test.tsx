import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProviderConfiguration } from "./provider-configuration";
import { applyDockerPreset, dockerMode } from "./docker-presets";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace("{{number}}", String(values?.number)),
  }),
}));
const presets = {
  standard: { image: "sandbox:dev", execution: "native", desktop: false },
  desktop: {
    image: "sandbox:dev-desktop",
    execution: "envd_http",
    desktop: true,
  },
};
const schema = {
  "x-primary-fields": ["image"],
  "x-environment-presets": presets,
  properties: {
    image: { type: "string", default: "sandbox:dev" },
    execution: { type: "string", default: "native" },
    desktop: { type: "boolean", default: false },
    environment: { type: "object", additionalProperties: { type: "string" } },
  },
};
function Editor({ readOnly = false }: { readOnly?: boolean }) {
  const [text, setText] = useState(
    JSON.stringify({ environment: { APP_MODE: "agent" } }),
  );
  return (
    <form aria-label="Template">
      <ProviderConfiguration
        schema={schema}
        variant="docker"
        text={text}
        onChange={setText}
        readOnly={readOnly}
      />
      <output data-testid="config">{text}</output>
    </form>
  );
}
const config = () =>
  JSON.parse(screen.getByTestId("config").textContent ?? "{}");
it("edits defaults in built-in presets, retains empty values, and removes variables", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  expect(
    (
      screen.getByRole("textbox", {
        name: "Variable name 1",
      }) as HTMLInputElement
    ).value,
  ).toBe("APP_MODE");
  await user.click(screen.getByRole("button", { name: "Add variable" }));
  await user.type(
    screen.getByRole("textbox", { name: "Variable name 2" }),
    "TOOL_ENDPOINT",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Variable value 2" }),
    "https://example.test",
  );
  expect(config().environment).toEqual({
    APP_MODE: "agent",
    TOOL_ENDPOINT: "https://example.test",
  });
  await user.clear(screen.getByRole("textbox", { name: "Variable value 2" }));
  expect(config().environment.TOOL_ENDPOINT).toBe("");
  await user.click(screen.getByRole("button", { name: "Remove variable 1" }));
  expect(config().environment).toEqual({ TOOL_ENDPOINT: "" });
});
it("keeps duplicate names visible and blocks invalid form submission", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add variable" }));
  await user.type(
    screen.getByRole("textbox", { name: "Variable name 2" }),
    "APP_MODE",
  );
  expect(screen.getAllByRole("alert")).toHaveLength(2);
  expect(
    (
      screen.getByRole("form", { name: "Template" }) as HTMLFormElement
    ).checkValidity(),
  ).toBe(false);
  await user.clear(screen.getByRole("textbox", { name: "Variable name 2" }));
  await user.type(
    screen.getByRole("textbox", { name: "Variable name 2" }),
    "OTHER",
  );
  expect(config().environment).toEqual({ APP_MODE: "agent", OTHER: "" });
});
it("preserves defaults when applying a Docker preset without forcing custom mode", () => {
  const environment = { APP_MODE: "agent" };
  expect(
    dockerMode({ ...presets.standard, environment }, schema, presets),
  ).toBe("standard");
  expect(applyDockerPreset({ environment }, presets.desktop)).toEqual({
    ...presets.desktop,
    environment,
  });
});
it("presents static values without editing controls for readers", () => {
  render(<Editor readOnly />);
  expect(screen.getByText("APP_MODE")).toBeTruthy();
  expect(screen.getByText("agent")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Variable name 1" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add variable" })).toBeNull();
});
