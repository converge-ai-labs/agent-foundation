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

it.each([
  ["Control", "ControlLeft"],
  ["Control", "ControlRight"],
  ["AltGraph", "AltRight"],
  ["Shift", "ShiftLeft"],
  ["a", "KeyA"],
])(
  "releases held %s through noVNC when focus leaves the desktop",
  (value, code) => {
    const released = vi.fn();
    canvas.addEventListener("keyup", released);
    const outside = document.createElement("button");
    document.body.append(outside);
    canvas.focus();
    key("keydown", value, code);
    outside.focus();
    expect(released).toHaveBeenCalledOnce();
    expect(released.mock.calls[0][0]).toMatchObject({ key: value, code });
    // The real release can arrive outside the desktop after its focusout.
    outside.dispatchEvent(new KeyboardEvent("keyup", { key: value, code }));
    binding.stop();
    expect(released).toHaveBeenCalledOnce();
    outside.remove();
  },
);

it.each(["blur", "hidden", "dispose"])(
  "releases physical keys on %s",
  (kind) => {
    const released = vi.fn();
    canvas.addEventListener("keyup", released);
    key("keydown", "Control", "ControlLeft");
    if (kind === "blur") window.dispatchEvent(new Event("blur"));
    else if (kind === "hidden") {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
    } else binding.stop();
    expect(released).toHaveBeenCalledOnce();
  },
);

it("retains keys when focus stays inside the desktop and forgets normal releases", () => {
  const released = vi.fn();
  canvas.addEventListener("keyup", released);
  const inside = document.createElement("button");
  root.append(inside);
  canvas.focus();
  key("keydown", "a", "KeyA");
  inside.focus();
  expect(released).not.toHaveBeenCalled();
  canvas.focus();
  key("keyup", "a", "KeyA");
  binding.stop();
  expect(released).toHaveBeenCalledOnce();
});
