// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { bindTextInput } from "./text-input";
import { bindKeyboard } from "./keyboard";

let root: HTMLDivElement;
let input: HTMLTextAreaElement;
let enabled: boolean;
let binding: ReturnType<typeof bindTextInput>;
let keyboard: ReturnType<typeof bindKeyboard>;
const sendKey = vi.fn();
const commit = vi.fn();
const nativeKey = vi.fn();
function key(type: "keydown" | "keyup", init: KeyboardEventInit) {
  const event = new KeyboardEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  input.dispatchEvent(event);
  return event;
}
function compose(type: string, data = "") {
  input.dispatchEvent(new CompositionEvent(type, { bubbles: true, data }));
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
  root = document.createElement("div");
  const canvas = document.createElement("canvas");
  canvas.addEventListener("keydown", nativeKey);
  canvas.addEventListener("keyup", nativeKey);
  root.append(canvas);
  document.body.append(root);
  enabled = true;
  const connection = { sendKey, focusOnClick: true };
  binding = bindTextInput({ root, active: () => enabled, connection, commit });
  keyboard = bindKeyboard({ root, active: () => enabled, connection });
  input = root.querySelector("textarea")!;
  binding.focus();
});
afterEach(() => {
  keyboard.stop();
  binding.stop();
  root.remove();
  vi.restoreAllMocks();
});

it("lets the local IME compose without sending preedit keys, then sends Unicode exactly once", () => {
  expect(document.activeElement).toBe(input);
  expect(
    key("keydown", { key: "Process", code: "KeyN", keyCode: 229 })
      .defaultPrevented,
  ).toBe(false);
  compose("compositionstart");
  key("keydown", { key: "n", code: "KeyN", isComposing: true });
  input.value = "nihao";
  input.dispatchEvent(
    new InputEvent("input", {
      data: "nihao",
      inputType: "insertCompositionText",
      isComposing: true,
    }),
  );
  expect(sendKey).not.toHaveBeenCalled();
  expect(nativeKey).not.toHaveBeenCalled();
  compose("compositionend", "你好🚀");
  input.dispatchEvent(
    new InputEvent("input", {
      data: "你好🚀",
      inputType: "insertFromComposition",
    }),
  );
  key("keyup", { key: "Enter", code: "Enter" });
  expect(commit.mock.calls).toEqual([["你好🚀"]]);
  expect(input.value).toBe("");
});

it("keeps ordinary keys, shortcuts and focus-loss releases with noVNC", () => {
  key("keydown", { key: "a", code: "KeyA" });
  key("keyup", { key: "a", code: "KeyA" });
  expect(
    nativeKey.mock.calls.map(([event]) => [event.type, event.code]),
  ).toEqual([
    ["keydown", "KeyA"],
    ["keyup", "KeyA"],
  ]);
  key("keydown", { key: "Meta", code: "MetaLeft" });
  expect(sendKey).toHaveBeenLastCalledWith(0xffe3, "ControlLeft", true);
  input.blur();
  expect(sendKey).toHaveBeenLastCalledWith(0xffe3, "ControlLeft", false);
  binding.focus();
  key("keydown", { key: "Shift", code: "ShiftLeft" });
  compose("compositionstart");
  expect(nativeKey).toHaveBeenLastCalledWith(
    expect.objectContaining({ type: "keyup", code: "ShiftLeft" }),
  );
});

it("ignores cancelled composition and stops accepting text after handback or disposal", () => {
  compose("compositionstart");
  compose("compositionend");
  expect(commit).not.toHaveBeenCalled();
  expect(sendKey).not.toHaveBeenCalled();
  enabled = false;
  compose("compositionend", "不要发送");
  expect(commit).not.toHaveBeenCalled();
  key("keydown", { key: "a", code: "KeyA" });
  expect(sendKey).not.toHaveBeenCalled();
  keyboard.stop();
  binding.stop();
  expect(root.querySelector("textarea")).toBeNull();
});
