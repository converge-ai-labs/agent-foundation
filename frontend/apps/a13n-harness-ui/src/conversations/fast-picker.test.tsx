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

const astra: Schema<"ModelSummary"> = {
  ...model,
  route: "openai-codex:gpt-6-astra",
  fast: { supported: true, state: "ultrafast", ultrafast_supported: true },
};

it("switches exclusively between inherited Ultrafast, Fast and Off, then resets", async () => {
  const user = userEvent.setup();
  function Control() {
    const [value, setValue] = useState<Schema<"SubmitRequest">["fast"]>(null);
    return <FastPicker model={astra} value={value} onChange={setValue} />;
  }
  render(<Control />);
  const fast = screen.getByRole("button", { name: "Fast mode" });
  const ultra = screen.getByRole("button", { name: "Ultrafast mode" });
  expect(screen.getByText("Default · Ultrafast")).toBeTruthy();
  expect(ultra.getAttribute("aria-pressed")).toBe("true");
  expect(fast.getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByText(/Pro \$500/)).toBeTruthy();
  await user.click(fast);
  expect(fast.getAttribute("aria-pressed")).toBe("true");
  expect(ultra.getAttribute("aria-pressed")).toBe("false");
  ultra.focus();
  await user.keyboard("[Space]");
  expect(ultra.getAttribute("aria-pressed")).toBe("true");
  expect(fast.getAttribute("aria-pressed")).toBe("false");
  await user.click(ultra);
  expect(screen.getByText("Off")).toBeTruthy();
  expect(ultra.getAttribute("aria-pressed")).toBe("false");
  await user.click(
    screen.getByRole("button", { name: "Use default Fast mode" }),
  );
  expect(screen.getByText("Default · Ultrafast")).toBeTruthy();
  expect(ultra.getAttribute("aria-pressed")).toBe("true");
});

it("disables an unsupported Ultrafast selection without misrepresenting it as Fast", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  const reason =
    "Ultrafast requires GPT-6 Astra through a Codex subscription connection.";
  const view = render(
    <FastPicker model={astra} value="ultrafast" onChange={change} />,
  );
  view.rerender(
    <FastPicker
      model={{
        ...model,
        fast: {
          supported: true,
          state: "off",
          ultrafast_supported: false,
          ultrafast_reason: reason,
        },
      }}
      value="ultrafast"
      onChange={change}
    />,
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Ultrafast mode",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "Fast mode" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  expect(screen.getByText(reason)).toBeTruthy();
  expect(screen.getByText("Unavailable selection — use default.")).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Use default Fast mode" }),
  );
  expect(change).toHaveBeenCalledWith(null);
  view.rerender(
    <FastPicker model={astra} value="ultrafast" disabled onChange={change} />,
  );
  for (const button of screen.getAllByRole("button"))
    expect((button as HTMLButtonElement).disabled).toBe(true);
});
