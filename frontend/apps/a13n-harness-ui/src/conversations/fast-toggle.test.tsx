// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { FastToggle } from "./fast-toggle";
import type { Schema } from "../transport/client";

afterEach(cleanup);
const model: Schema<"ModelSummary"> = {
  model_id: "model",
  name: "Model",
  route: "openai:gpt-5",
  fast: { supported: true, state: "on" },
};

it("inherits Fast on and toggles two states with a stable label and no reset", async () => {
  const user = userEvent.setup();
  function Control() {
    const [value, setValue] = useState<boolean | null>(null);
    return <FastToggle model={model} value={value} onChange={setValue} />;
  }
  render(<Control />);
  const toggle = screen.getByRole("button", { name: "Fast mode" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(toggle.getAttribute("data-active")).toBe("true");
  expect(toggle.title).toContain("Model setting: Fast on");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getAllByRole("button")).toHaveLength(1);
  await user.click(toggle);
  expect(toggle.textContent).toBe("Fast");
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(toggle.getAttribute("data-active")).toBe("false");
  toggle.focus();
  await user.keyboard("[Space]");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
});

it.each(["default", "off"] as const)(
  "leaves inherited %s unlit and enables Fast on click",
  async (state) => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <FastToggle
        model={{ ...model, fast: { supported: true, state } }}
        onChange={change}
      />,
    );
    const toggle = screen.getByRole("button", { name: "Fast mode" });
    expect(toggle.textContent).toBe("Fast");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(change).not.toHaveBeenCalled();
    await user.click(toggle);
    expect(change).toHaveBeenLastCalledWith(true);
  },
);

it("follows changed model settings without overriding explicit Off", () => {
  const change = vi.fn();
  const view = render(<FastToggle model={model} onChange={change} />);
  const toggle = screen.getByRole("button", { name: "Fast mode" });
  view.rerender(
    <FastToggle
      model={{ ...model, fast: { supported: true, state: "off" } }}
      onChange={change}
    />,
  );
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  view.rerender(<FastToggle model={model} value={false} onChange={change} />);
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(change).not.toHaveBeenCalled();
});

it("explains unavailable controls and cannot toggle them", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(
    <FastToggle
      model={{
        ...model,
        fast: {
          supported: false,
          state: "default",
          reason: "Unavailable connection",
        },
      }}
      onChange={change}
    />,
  );
  const toggle = screen.getByRole("button", {
    name: "Fast mode",
  }) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(toggle.title).toBe("Unavailable connection");
  await user.click(toggle);
  expect(change).not.toHaveBeenCalled();
});
