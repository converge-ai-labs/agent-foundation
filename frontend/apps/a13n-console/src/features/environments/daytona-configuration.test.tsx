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
  required: ["snapshot"],
  "x-primary-fields": ["desktop", "snapshot"],
  properties: {
    snapshot: { type: "string", title: "Snapshot", minLength: 1 },
    desktop: { type: "boolean", title: "Desktop environment", default: false },
    desktop_assistance: {
      type: "boolean",
      title: "Desktop assistance and control",
      default: false,
    },
  },
};
function Editor() {
  const [text, setText] = useState(
    JSON.stringify({
      snapshot: "my-envd",
      desktop: true,
      desktop_assistance: true,
    }),
  );
  return (
    <>
      <ProviderConfiguration
        variant="daytona"
        schema={schema}
        text={text}
        onChange={setText}
      />
      <output data-testid="config">{text}</output>
    </>
  );
}
it("keeps the Envd snapshot while disabling desktop and human access", async () => {
  const user = userEvent.setup();
  render(<Editor />);
  await user.click(
    screen.getByRole("checkbox", { name: "Desktop environment" }),
  );
  expect(JSON.parse(screen.getByTestId("config").textContent ?? "{}")).toEqual({
    snapshot: "my-envd",
    desktop: false,
    desktop_assistance: false,
  });
  expect(screen.queryByText("Desktop assistance and control")).toBeNull();
  expect(
    (screen.getByRole("textbox", { name: "Snapshot" }) as HTMLInputElement)
      .value,
  ).toBe("my-envd");
});
