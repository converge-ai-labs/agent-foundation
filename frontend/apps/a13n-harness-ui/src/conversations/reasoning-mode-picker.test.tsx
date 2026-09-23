// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  ModelControlPanel,
  type ModelControlValues,
  modelControlRequest,
} from "./model-controls";
import { ReasoningModePicker } from "./reasoning-mode-picker";

afterEach(cleanup);

it("distinguishes authored Pro from explicit Standard and restores the Model default", async () => {
  const user = userEvent.setup();
  function Panel() {
    const [controls, setControls] = useState<ModelControlValues>({
      thinking: "low",
      fast: false,
    });
    return (
      <>
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
        <output>{JSON.stringify(modelControlRequest(controls))}</output>
      </>
    );
  }
  render(<Panel />);
  const section = within(
    screen.getByRole("region", { name: "Reasoning mode" }),
  );
  expect(section.getByText("Model default: Pro")).toBeTruthy();
  expect(
    section
      .getByRole("button", { name: "Using default" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(section.getByRole("button", { name: "Standard" }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    thinking: "low",
    fast: false,
    reasoning_mode: "standard",
  });
  expect(
    section.getByText(/Model default: Pro.*Override for next run/),
  ).toBeTruthy();
  await user.click(section.getByRole("button", { name: "Use default" }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    thinking: "low",
    fast: false,
  });
});

it("keeps a stale unsupported selection resettable without claiming it is effective", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(
    <ReasoningModePicker
      control={{
        supported: false,
        state: "custom",
        reason: "Controlled by extra_body",
      }}
      value="pro"
      onChange={change}
    />,
  );
  expect(
    (screen.getByRole("button", { name: "Standard" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText(/Unavailable selection/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Use default" }));
  expect(change).toHaveBeenCalledWith(null);
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
  expect(screen.getByText("Model default: Provider default")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Pro" }));
  expect(change).not.toHaveBeenCalled();
});
