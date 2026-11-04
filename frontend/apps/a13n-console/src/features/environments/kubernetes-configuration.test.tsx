import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { KubernetesConfiguration } from "./kubernetes-configuration";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const direct = {
  pod_template: {
    spec: { containers: [{ name: "envd", image: "sandbox:test" }] },
  },
  execution: { container: "envd", connection: { strategy: "direct" } },
};
const schema = {
  "x-environment-presets": {
    direct,
    port_forward: {
      ...direct,
      execution: {
        container: "envd",
        connection: { strategy: "port_forward" },
      },
    },
  },
};

it("starts an empty native recipe without overwriting a customized manifest", async () => {
  function Editor() {
    const [text, setText] = useState("{}");
    return (
      <KubernetesConfiguration schema={schema} text={text} onChange={setText} />
    );
  }
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Local port-forward starter" }),
  );
  expect(
    (
      screen.getByRole("textbox", {
        name: "Native recipe JSON",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe(JSON.stringify(schema["x-environment-presets"].port_forward, null, 2));
  expect(
    screen.queryByRole("button", { name: "In-cluster starter" }),
  ).toBeNull();
});

it.each(["{", '{"pod_template":{"metadata":{"labels":{"team":"research"}}}}'])(
  "preserves incomplete or populated JSON: %s",
  (text) => {
    const change = vi.fn();
    render(
      <KubernetesConfiguration schema={schema} text={text} onChange={change} />,
    );
    expect(
      (
        screen.getByRole("textbox", {
          name: "Native recipe JSON",
        }) as HTMLTextAreaElement
      ).value,
    ).toBe(text);
    expect(
      screen.queryByRole("button", { name: "In-cluster starter" }),
    ).toBeNull();
    expect(change).not.toHaveBeenCalled();
  },
);
