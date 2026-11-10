import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProviderConfiguration } from "./provider-configuration";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const schema = {
  type: "object",
  properties: {
    image: { type: "string", title: "Image", default: "auto" },
    execution: {
      type: "string",
      title: "Execution connection",
      enum: ["native", "envd_http", "envd_ws"],
      default: "envd_http",
    },
    envd_management: {
      type: "string",
      title: "Envd binary management",
      enum: ["managed", "preinstalled"],
      default: "preinstalled",
    },
    startup_command: {
      anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }],
      title: "Startup command",
      default: null,
    },
    desktop: { type: "boolean", default: false },
    desktop_assistance: { type: "boolean", default: false },
  },
};
function Editor({ initial = {} }: { initial?: Record<string, unknown> }) {
  const [text, setText] = useState(JSON.stringify(initial));
  return (
    <>
      <ProviderConfiguration
        variant="modal"
        schema={schema}
        text={text}
        onChange={setText}
      />
      <output data-testid="config">{text}</output>
    </>
  );
}
const configuration = () =>
  JSON.parse(screen.getByTestId("config").textContent ?? "{}");
it("one desktop toggle enables automatic image preparation and human control", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: true,
    image: "auto",
    desktop_assistance: true,
    cpu: 2,
    memory: 4096,
  });
  expect(screen.queryByRole("textbox", { name: "Image" })).toBeNull();
  expect(screen.getByText(/first Modal desktop creation/)).toBeTruthy();
});
it("retains a custom image and resource choices when enabling desktop", async () => {
  render(<Editor initial={{ image: "im-custom", cpu: 3, memory: 5000 }} />);
  await userEvent
    .setup()
    .click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    image: "im-custom",
    cpu: 3,
    memory: 5000,
    desktop: true,
  });
});
it("preserves the custom draft when switching between automatic and custom", async () => {
  render(<Editor initial={{ desktop: true, image: "im-custom" }} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Automatic Envd" }));
  expect(configuration().image).toBe("auto");
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  expect(configuration().image).toBe("im-custom");
});

it("offers headless automatic images and preserves a managed base across custom image drafts", async () => {
  const base = `debian@sha256:${"a".repeat(64)}`;
  render(
    <Editor initial={{ image: "auto", base_image: base, desktop: false }} />,
  );
  const user = userEvent.setup();
  expect(
    screen.getByText(/first headless creation builds an Envd image/),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  expect(configuration().base_image).toBeUndefined();
  await user.type(screen.getByRole("textbox", { name: "Image" }), "im-team");
  await user.click(screen.getByRole("button", { name: "Automatic Envd" }));
  expect(configuration()).toMatchObject({
    image: "auto",
    base_image: base,
    desktop: false,
  });
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  expect(configuration()).toMatchObject({ image: "im-team", desktop: false });
  expect(configuration().base_image).toBeUndefined();
});

it("restores authored startup and ownership after automatic mode, including normalized saved recipes", async () => {
  const startup = ["/opt/team/start-envd", "--background"];
  render(
    <Editor
      initial={{
        image: "im-custom",
        envd_management: "preinstalled",
        startup_command: startup,
      }}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Automatic Envd" }));
  expect(configuration()).toMatchObject({
    image: "auto",
    envd_management: "managed",
  });
  expect(configuration().startup_command).toBeUndefined();
  expect(screen.queryByText("Startup command")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  expect(configuration()).toMatchObject({
    image: "im-custom",
    envd_management: "preinstalled",
    startup_command: startup,
  });
});

it("shows the effective managed default without changing the draft on disclosure", async () => {
  render(<Editor />);
  await userEvent.setup().click(
    screen.getByRole("button", {
      name: "Advanced environment configuration",
    }),
  );
  expect(
    screen.getByRole("combobox", { name: "Envd binary management" })
      .textContent,
  ).toContain("Managed");
  expect(configuration()).toEqual({});
});

it("preserves custom managed image ownership when changing the Envd transport", async () => {
  render(
    <Editor
      initial={{
        image: "im-team",
        execution: "envd_http",
        envd_management: "managed",
        startup_command: null,
      }}
    />,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("combobox", { name: "Execution connection" }),
  );
  await user.click(screen.getByRole("option", { name: "Envd ws" }));
  expect(configuration()).toMatchObject({
    image: "im-team",
    execution: "envd_ws",
    envd_management: "managed",
  });
  expect(configuration().startup_command).toBeUndefined();
});
