// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { FastPicker } from "./fast-picker";
import type { Schema } from "../transport/client";

afterEach(cleanup);
const model: Schema<"ModelSummary"> = {
  model_id: "model",
  name: "Model",
  route: "openai:gpt-5",
  fast: { supported: true, state: "on" },
};

it("toggles inherited On to explicit Off and restores inheritance", async () => {
  const user = userEvent.setup();
  function Control() {
    const [value, setValue] = useState<boolean | null>(null);
    return <FastPicker model={model} value={value} onChange={setValue} />;
  }
  render(<Control />);
  const inherited = screen.getByRole("button", {
    name: "Use default Fast mode",
  });
  const toggle = screen.getByRole("button", { name: "Fast mode" });
  expect(inherited.getAttribute("aria-pressed")).toBe("true");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  await user.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(inherited.getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByText("Off")).toBeTruthy();
  toggle.focus();
  await user.keyboard("[Space]");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  await user.click(inherited);
  expect(inherited.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("Default · On")).toBeTruthy();
});

it.each(["default", "off"] as const)(
  "does not mistake inherited %s for explicit Off",
  async (state) => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <FastPicker
        model={{ ...model, fast: { supported: true, state } }}
        onChange={change}
      />,
    );
    expect(
      screen
        .getByRole("button", { name: "Use default Fast mode" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByText(
        state === "default" ? "Default · Provider default" : "Default · Off",
      ),
    ).toBeTruthy();
    expect(change).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Fast mode" }));
    expect(change).toHaveBeenLastCalledWith(true);
  },
);

it("follows changed model settings without overriding explicit Off", () => {
  const change = vi.fn();
  const view = render(<FastPicker model={model} onChange={change} />);
  view.rerender(
    <FastPicker
      model={{ ...model, fast: { supported: true, state: "off" } }}
      onChange={change}
    />,
  );
  expect(screen.getByText("Default · Off")).toBeTruthy();
  view.rerender(<FastPicker model={model} value={false} onChange={change} />);
  expect(
    screen
      .getByRole("button", { name: "Fast mode" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  expect(screen.getByText("Off")).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
});

it("explains unavailable controls and only allows resetting a stale override", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(
    <FastPicker
      model={{
        ...model,
        fast: {
          supported: false,
          state: "default",
          reason: "Unavailable connection",
        },
      }}
      value
      onChange={change}
    />,
  );
  const toggle = screen.getByRole("button", {
    name: "Fast mode",
  }) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(screen.getByText("Unavailable connection")).toBeTruthy();
  await user.click(toggle);
  expect(change).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Use default Fast mode" }),
  );
  expect(change).toHaveBeenCalledWith(null);
});

it("locks both the toggle and default reset during submission", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(<FastPicker model={model} value={false} disabled onChange={change} />);
  for (const button of screen.getAllByRole("button")) {
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await user.click(button);
  }
  expect(change).not.toHaveBeenCalled();
});
