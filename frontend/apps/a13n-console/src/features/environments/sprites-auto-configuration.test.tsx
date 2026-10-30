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
    execution: { type: "string", default: "native" },
    desktop: { type: "boolean", default: false },
    desktop_assistance: { type: "boolean", default: false },
  },
};
function Editor({ initial = {} }: { initial?: Record<string, unknown> }) {
  const [text, setText] = useState(JSON.stringify(initial));
  return (
    <>
      <ProviderConfiguration
        variant="sprites"
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
it("one desktop toggle enables per-sandbox installation and human control", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: true,

    execution: "envd_http",
    desktop_assistance: true,
  });
  expect(screen.queryByRole("textbox", { name: "Snapshot" })).toBeNull();
  expect(screen.getByText(/Each new Sprites desktop/)).toBeTruthy();
});

it("switching off desktop restores native execution without losing region", async () => {
  render(
    <Editor
      initial={{
        desktop: true,
        execution: "envd_http",
        desktop_assistance: true,
        region: "ord",
      }}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("switch", { name: "Desktop environment" }));
  expect(configuration()).toMatchObject({
    desktop: false,
    execution: "native",
    desktop_assistance: false,
    region: "ord",
  });
});
