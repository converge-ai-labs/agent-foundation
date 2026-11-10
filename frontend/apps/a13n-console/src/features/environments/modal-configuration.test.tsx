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
    execution: {
      type: "string",
      enum: ["native", "envd_http", "envd_ws"],
      default: "envd_http",
    },
    image: { type: "string", title: "Image", default: "auto" },
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
it("selects native without keeping desktop preparation and requires an explicit image", async () => {
  render(
    <Editor
      initial={{
        desktop: true,
        desktop_assistance: true,
        image: "auto",
        cpu: 3,
      }}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Native" }));
  expect(configuration()).toMatchObject({
    execution: "native",
    desktop: false,
    desktop_assistance: false,
    image: "",
    cpu: 3,
  });
  expect(
    screen
      .getByRole("switch", { name: "Desktop environment" })
      .getAttribute("aria-disabled"),
  ).toBe("true");
  await user.type(
    screen.getByRole("textbox", { name: "Image" }),
    "python:3.13-slim",
  );
  await user.click(screen.getByRole("button", { name: "Envd HTTP" }));
  expect(configuration()).toMatchObject({
    execution: "envd_http",
    image: "python:3.13-slim",
    cpu: 3,
  });
  expect(
    screen
      .getByRole("switch", { name: "Desktop environment" })
      .getAttribute("aria-disabled"),
  ).not.toBe("true");
});
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
it("keeps native settings as a draft while selecting an Envd connection", async () => {
  render(
    <Editor
      initial={{
        execution: "native",
        image: "python:3.13-slim",
        root: "/custom",
        cpu: 3,
      }}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Envd WebSocket" }));
  expect(configuration().root).toBeUndefined();
  expect(configuration().cpu).toBe(3);
  await user.click(screen.getByRole("button", { name: "Native" }));
  expect(configuration()).toMatchObject({
    execution: "native",
    image: "python:3.13-slim",
    root: "/custom",
    cpu: 3,
  });
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
  await user.click(screen.getByRole("button", { name: "Automatic desktop" }));
  expect(configuration().image).toBe("auto");
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  expect(configuration().image).toBe("im-custom");
});
