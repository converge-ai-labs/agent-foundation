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

it("distinguishes inherited On, explicit Off and restored inheritance", async () => {
  const user = userEvent.setup();
  function Control() {
    const [value, setValue] = useState<boolean | null>(null);
    return <FastPicker model={model} value={value} onChange={setValue} />;
  }
  render(<Control />);
  const inherited = screen.getByRole("button", { name: /^Default.*On$/ });
  expect(inherited.getAttribute("aria-pressed")).toBe("true");
  const off = screen.getByRole("button", { name: "Off" });
  await user.click(off);
  expect(off.getAttribute("aria-pressed")).toBe("true");
  expect(inherited.getAttribute("aria-pressed")).toBe("false");
  screen.getByRole("button", { name: "On" }).focus();
  await user.keyboard("[Space]");
  expect(
    screen.getByRole("button", { name: "On" }).getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(inherited);
  expect(inherited.getAttribute("aria-pressed")).toBe("true");
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
        .getByRole("button", { name: /^Default/ })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "Off" }).getAttribute("aria-pressed"),
    ).toBe("false");
    expect(change).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "On" }));
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
  expect(
    screen
      .getByRole("button", { name: /^Default.*Off$/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  view.rerender(<FastPicker model={model} value={false} onChange={change} />);
  expect(
    screen.getByRole("button", { name: "Off" }).getAttribute("aria-pressed"),
  ).toBe("true");
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
  const on = screen.getByRole("button", { name: "On" }) as HTMLButtonElement;
  expect(on.disabled).toBe(true);
  expect(screen.getByText("Unavailable connection")).toBeTruthy();
  await user.click(on);
  expect(change).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /^Default/ }));
  expect(change).toHaveBeenCalledWith(null);
});
