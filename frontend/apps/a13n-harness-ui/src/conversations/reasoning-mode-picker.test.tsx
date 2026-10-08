// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  ModelControlPanel,
  type ModelControlValues,
  modelControlRequest,
} from "./model-controls";
import { ComposerSettings } from "./composer-settings";
import { ReasoningModePicker } from "./reasoning-mode-picker";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("distinguishes authored Pro from explicit Standard and restores the Model default", async () => {
  const user = userEvent.setup();
  function Panel() {
    const [controls, setControls] = useState<ModelControlValues>({
      thinking: "low",
      fast: false,
    });
    return (
      <>
        <ComposerSettings>
          <ModelControlPanel
            model={{
              model_id: "model",
              name: "Model",
              route: "openai:gpt-5.6-sol",
              reasoning_mode: { supported: true, state: "pro" },
            }}
            controls={controls}
            onControlsChange={setControls}
          />
        </ComposerSettings>
        <output>{JSON.stringify(modelControlRequest(controls))}</output>
      </>
    );
  }
  render(<Panel />);
  await user.click(
    screen.getByRole("button", { name: "Agent & Model settings" }),
  );
  await user.click(screen.getByRole("button", { name: "Reasoning mode" }));
  expect(
    screen
      .getByRole("button", { name: /^Default/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: "Standard" }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    thinking: "low",
    fast: false,
    reasoning_mode: "standard",
  });
  expect(
    screen.getByRole("button", { name: "Reasoning mode" }).textContent,
  ).toBe("Reasoning modeStandard");
  await user.click(screen.getByRole("button", { name: "Reasoning mode" }));
  await user.click(screen.getByRole("button", { name: /^Default/ }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    thinking: "low",
    fast: false,
  });
});

it("does not interpret provider default as Standard and locks changes when disabled", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(
    <ReasoningModePicker
      control={{ supported: true, state: "default" }}
      disabled
      onChange={change}
    />,
  );
  expect(screen.getByText("Provider default")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Pro" }));
  expect(change).not.toHaveBeenCalled();
});

it.each([
  ["pro", "Pro · Model default"],
  ["standard", "Standard · Model default"],
  ["default", "Provider default"],
] as const)(
  "shows inherited %s directly in the control row",
  async (state, label) => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <ComposerSettings>
        <ModelControlPanel
          model={{
            model_id: "model",
            name: "Model",
            route: "openai:gpt-5.6-sol",
            reasoning_mode: { supported: true, state },
          }}
          controls={{}}
          onControlsChange={change}
        />
      </ComposerSettings>,
    );
    await user.click(
      screen.getByRole("button", { name: "Agent & Model settings" }),
    );
    expect(
      screen.getByRole("button", { name: "Reasoning mode" }).textContent,
    ).toBe(`Reasoning mode${label}`);
    expect(change).not.toHaveBeenCalled();
  },
);
