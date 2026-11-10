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
      title: "Execution connection",
      enum: ["native", "envd_http", "envd_ws"],
      default: "native",
    },
    snapshot: { type: "string", title: "Snapshot", default: "auto" },
    desktop: { type: "boolean", default: false },
    desktop_assistance: { type: "boolean", default: false },
  },
};
function Editor({ initial = {} }: { initial?: Record<string, unknown> }) {
  const [text, setText] = useState(JSON.stringify(initial));
  return (
    <>
      <ProviderConfiguration
        variant="vercel"
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
it("one desktop toggle enables direct target preparation and human control", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: true,
    snapshot: "auto",
    execution: "envd_http",
    desktop_assistance: true,
  });
  expect(screen.queryByRole("textbox", { name: "Snapshot" })).toBeNull();
  expect(
    screen.getByText(/installed directly on each persistent Vercel sandbox/),
  ).toBeTruthy();
});
it("retains a custom snapshot and resource choices when enabling desktop", async () => {
  render(
    <Editor initial={{ snapshot: "custom", request_timeout_seconds: 80 }} />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    snapshot: "custom",
    request_timeout_seconds: 80,
    desktop: true,
  });
});
it("preserves the custom draft when switching between automatic and custom", async () => {
  render(<Editor initial={{ desktop: true, snapshot: "im-custom" }} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Automatic Envd" }));
  expect(configuration().snapshot).toBe("auto");
  await user.click(screen.getByRole("button", { name: "Custom snapshot" }));
  expect(configuration().snapshot).toBe("im-custom");
});

it("switching desktop off restores the native configuration", async () => {
  render(
    <Editor
      initial={{
        desktop: true,
        execution: "envd_http",
        snapshot: "auto",
        desktop_assistance: true,
      }}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: false,
    snapshot: null,
    execution: "native",
    desktop_assistance: false,
  });
});

it("lets a managed desktop select outbound WebSocket", async () => {
  render(
    <Editor
      initial={{ desktop: true, execution: "envd_http", snapshot: "auto" }}
    />,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("combobox", { name: "Execution connection" }),
  );
  await user.click(screen.getByRole("option", { name: "Envd ws" }));
  expect(configuration()).toMatchObject({
    execution: "envd_ws",
    desktop: true,
    snapshot: "auto",
  });
});

it("selects direct headless preparation without enabling desktop and preserves snapshot drafts", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("combobox", { name: "Execution connection" }),
  );
  await user.click(screen.getByRole("option", { name: "Envd HTTP" }));
  expect(configuration()).toMatchObject({
    execution: "envd_http",
    snapshot: "auto",
    timeout_seconds: 2700,
    desktop_assistance: false,
  });
  expect(configuration().desktop).not.toBe(true);
  expect(
    screen.getByText(/installed directly on each persistent Vercel sandbox/),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Custom snapshot" }));
  await user.type(
    screen.getByRole("textbox", { name: "Snapshot" }),
    "snap-team",
  );
  await user.click(screen.getByRole("button", { name: "Automatic Envd" }));
  await user.click(screen.getByRole("button", { name: "Custom snapshot" }));
  expect(configuration().snapshot).toBe("snap-team");
});
