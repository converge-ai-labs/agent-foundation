// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Schema } from "../transport/client";
import { i18n } from "../i18n";
import { ThinkingPicker } from "./thinking-picker";
import { useComposerRunChoices } from "./composer-run-choices";
import { ThreadDraft } from "./draft";
import * as Y from "yjs";
import { renderHook } from "@testing-library/react";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  })),
);
afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

const model: Schema<"ModelSummary"> = {
  model_id: "custom-model",
  name: "Custom",
  route: "custom:provider-independent",
  thinking: {
    status: "supported",
    default_summary: "Budget - 8,192 tokens",
    options: [
      { value: null, label: "Model default", description: "Configured budget" },
      { value: false, label: "No thinking", description: "Disable thinking" },
      { value: "low", label: "Quick", description: "Budget: 2,048 tokens" },
      {
        value: "high",
        label: "Deep",
        description: "Budget: 16,384 tokens",
        disabled_reason: "Increase the output limit first",
      },
    ],
  },
};

it("renders backend labels and preserves false separately from default", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const view = render(<ThinkingPicker model={model} onChange={onChange} />);
  await user.click(screen.getByRole("button", { name: /No thinking/ }));
  expect(onChange).toHaveBeenLastCalledWith(false);
  view.unmount();
  render(<ThinkingPicker model={model} value={false} onChange={onChange} />);
  expect(
    screen
      .getByRole("button", { name: /No thinking/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  const blocked = screen.getByRole("button", { name: /Deep/ });
  expect(blocked.hasAttribute("disabled")).toBe(true);
  await user.click(blocked);
  expect(onChange).toHaveBeenCalledTimes(1);
  await user.click(
    screen.getByRole("button", { name: "Use default thinking" }),
  );
  expect(onChange).toHaveBeenLastCalledWith(null);
});

it("retains shared choices only for the matching Agent and effective Model", () => {
  localStorage.clear();
  const draft = new ThreadDraft();
  const props = { id: "first", draft, model: "original", agent: "writer" };
  const { result, rerender } = renderHook(
    (p) => useComposerRunChoices(p.draft, p.agent, p.model),
    { initialProps: props },
  );
  act(() =>
    result.current.onControlsChange({
      thinking: false,
      fast: false,
      reasoning_mode: "pro",
    }),
  );
  const restored = new ThreadDraft();
  Y.applyUpdate(restored.optionsDoc, Y.encodeStateAsUpdate(draft.optionsDoc));
  rerender({ ...props, draft: restored });
  expect(restored.controls).toMatchObject({
    thinking: false,
    fast: false,
    reasoning_mode: "pro",
  });
  act(() => result.current.onModelChange("original"));
  expect(restored.controls.thinking).toBe(false);
  rerender({ ...props, draft: restored, agent: "reviewer" });
  expect(restored.modelOptions).toBeUndefined();
  act(() => result.current.onModelChange(undefined));
  act(() => result.current.onControlsChange({ thinking: "low" }));
  rerender({
    ...props,
    draft: restored,
    agent: "reviewer",
    model: "new-default",
  });
  expect(restored.modelOptions).toBeUndefined();
  const other = new ThreadDraft();
  rerender({ ...props, draft: other, id: "second" });
  expect(other.modelOptions).toBeUndefined();
});
it("renders boolean controls without inventing effort levels", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <ThinkingPicker
      model={{
        ...model,
        thinking: {
          status: "supported",
          default_summary: "Off",
          options: [
            {
              value: null,
              label: "Default",
              description: "Follow model settings",
            },
            { value: false, label: "Off", description: "Disable thinking" },
            { value: true, label: "On", description: "Enable thinking" },
          ],
        },
      }}
      onChange={onChange}
    />,
  );
  screen.getByRole("button", { name: /^Off/ }).focus();
  await user.keyboard("[ArrowRight][Space]");
  expect(onChange).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole("button", { name: "High" })).toBeNull();
});

it("keeps inherited defaults distinct from explicit levels across catalog refresh", () => {
  const change = vi.fn();
  const withDefault = (
    default_value: false | "low" | null,
  ): Schema<"ModelSummary"> => ({
    ...model,
    thinking: { ...model.thinking!, default_value },
  });
  const view = render(
    <ThinkingPicker model={withDefault("low")} onChange={change} />,
  );
  const pressed = (name: string) =>
    screen.getByRole("button", { name }).getAttribute("aria-pressed");
  expect(pressed("Quick")).toBe("false");
  expect(pressed("Use default thinking")).toBe("true");
  view.rerender(
    <ThinkingPicker model={withDefault(false)} onChange={change} />,
  );
  expect(pressed("No thinking")).toBe("false");
  expect(pressed("Use default thinking")).toBe("true");
  expect(pressed("Quick")).toBe("false");
  view.rerender(<ThinkingPicker model={withDefault(null)} onChange={change} />);
  expect(pressed("No thinking")).toBe("false");
  expect(pressed("Quick")).toBe("false");
  view.rerender(
    <ThinkingPicker
      model={withDefault("low")}
      value={false}
      onChange={change}
    />,
  );
  expect(pressed("No thinking")).toBe("true");
  expect(pressed("Quick")).toBe("false");
  expect(pressed("Use default thinking")).toBe("false");
  expect(change).not.toHaveBeenCalled();
});

it("keeps opaque backend labels and stale values while localizing their recovery", async () => {
  const change = vi.fn();
  render(<ThinkingPicker model={model} value="max" onChange={change} />);
  await act(() => i18n.changeLanguage("zh-CN"));
  const inherited = screen.getByRole("button", { name: "使用默认思考设置" });
  await userEvent.hover(inherited);
  expect(await screen.findByText("预算 - 8,192 token")).toBeTruthy();
  expect(inherited.hasAttribute("title")).toBe(false);
  expect(inherited.getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByRole("button", { name: "Quick" })).toBeTruthy();
  const disabled = screen.getByRole("button", { name: "Deep" });
  expect(disabled.getAttribute("title")).toBe(
    "Increase the output limit first",
  );
  expect(disabled).toHaveProperty("disabled", true);
  expect(screen.getByRole("alert").textContent).toBe(
    "所选项不可用，请使用默认设置。",
  );
  expect(change).not.toHaveBeenCalled();
  await userEvent.click(inherited);
  expect(change).toHaveBeenCalledExactlyOnceWith(null);
});
