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

it("toggles directly without opening a model popup and distinguishes reset from Off", async () => {
  const user = userEvent.setup();
  function Control() {
    const [value, setValue] = useState<boolean | null>(null);
    return <FastToggle model={model} value={value} onChange={setValue} />;
  }
  render(<Control />);
  const toggle = screen.getByRole("button", { name: "Fast mode" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(toggle.title).toContain("Model default: on");
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(toggle);
  expect(toggle.textContent).toContain("Fast Off");
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  await user.click(
    screen.getByRole("button", { name: "Use model default for Fast" }),
  );
  expect(toggle.textContent).toContain("Fast On");
  toggle.focus();
  await user.keyboard("[Space]");
  expect(toggle.textContent).toContain("Fast Off");
});

it("does not claim Off for an unknown default, and explains unavailable controls", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  const view = render(
    <FastToggle
      model={{ ...model, fast: { supported: true, state: "default" } }}
      onChange={change}
    />,
  );
  expect(screen.getByText("Fast Default")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Fast mode" }));
  expect(change).toHaveBeenLastCalledWith(true);
  view.rerender(
    <FastToggle
      model={{
        ...model,
        fast: {
          supported: false,
          state: "default",
          reason: "Unavailable connection",
        },
      }}
      value={true}
      onChange={change}
    />,
  );
  const toggle = screen.getByRole("button", {
    name: "Fast mode",
  }) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(toggle.title).toBe("Unavailable connection");
  await user.click(
    screen.getByRole("button", { name: "Use model default for Fast" }),
  );
  expect(change).toHaveBeenLastCalledWith(null);
});
