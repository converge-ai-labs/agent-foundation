// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { bindKeyboard } from "./keyboard";

let root: HTMLDivElement;
let canvas: HTMLCanvasElement;
let binding: ReturnType<typeof bindKeyboard>;
let active: boolean;
const sendKey = vi.fn();
const nativeKey = vi.fn();
function key(type: "keydown" | "keyup", value: string, code: string) {
  canvas.dispatchEvent(
    new KeyboardEvent(type, {
      key: value,
      code,
      bubbles: true,
      cancelable: true,
    }),
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
  root = document.createElement("div");
  canvas = document.createElement("canvas");
  canvas.tabIndex = 0;
  root.append(canvas);
  document.body.append(root);
  canvas.addEventListener("keydown", nativeKey);
  active = true;
  binding = bindKeyboard({
    root,
    active: () => active,
    connection: { sendKey },
  });
});
afterEach(() => {
  binding.stop();
  root.remove();
  vi.restoreAllMocks();
});
it("maps both Command keys to one Ctrl hold and leaves ordinary keys with noVNC", () => {
  key("keydown", "Meta", "MetaLeft");
  key("keydown", "Meta", "MetaRight");
  key("keydown", "l", "KeyL");
  expect(nativeKey).toHaveBeenCalledOnce();
  key("keyup", "Meta", "MetaLeft");
  expect(sendKey.mock.calls).toEqual([[0xffe3, "ControlLeft", true]]);
  key("keyup", "Meta", "MetaRight");
  expect(sendKey).toHaveBeenLastCalledWith(0xffe3, "ControlLeft", false);
});
it("preserves a held modifier across copy, then releases after Command is let go", () => {
  key("keydown", "Meta", "MetaLeft");
  binding.shortcut("copy");
  expect(sendKey.mock.calls).not.toContainEqual([0xffe3, "ControlLeft", false]);
  key("keyup", "Meta", "MetaLeft");
  binding.shortcut("paste");
  expect(sendKey.mock.calls.slice(-3)).toEqual([
    [0xffe3, "ControlLeft", true],
    [118, "KeyV"],
    [0xffe3, "ControlLeft", false],
  ]);
});
it.each(["blur", "focusout", "dispose"])("releases Command on %s", (kind) => {
  canvas.focus();
  key("keydown", "Meta", "MetaLeft");
  if (kind === "blur") window.dispatchEvent(new Event("blur"));
  else if (kind === "focusout") canvas.blur();
  else binding.stop();
  expect(sendKey).toHaveBeenLastCalledWith(0xffe3, "ControlLeft", false);
});
it("leaves Windows Ctrl/AltGr with noVNC and never sends without authority", () => {
  binding.stop();
  vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
  binding = bindKeyboard({
    root,
    active: () => active,
    connection: { sendKey },
  });
  key("keydown", "Control", "ControlLeft");
  binding.shortcut("copy");
  expect(nativeKey).toHaveBeenCalledOnce();
  expect(sendKey.mock.calls).not.toContainEqual([0xffe3, "ControlLeft", false]);
  key("keyup", "Control", "ControlLeft");
  active = false;
  sendKey.mockClear();
  binding.shortcut("paste");
  expect(sendKey).not.toHaveBeenCalled();
});
