import { expect, it } from "vitest";
import { terminalShortcut, terminalShortcutLabels } from "./terminal-shortcuts";

const key = {
  key: "",
  code: "",
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  isComposing: false,
};

it.each([false, true])("uses Control for panel shortcuts on mac=%s", (mac) => {
  expect(
    terminalShortcut({ ...key, code: "Backquote", ctrlKey: true }, mac),
  ).toBe("toggle");
  expect(
    terminalShortcut({ ...key, key: "~", ctrlKey: true, shiftKey: true }, mac),
  ).toBe("create");
  expect(
    terminalShortcut({ ...key, code: "Backquote", metaKey: true }, mac),
  ).toBeNull();
});

it.each([false, true])(
  "matches platform shortcuts without swallowing shell or composing input on mac=%s",
  (mac) => {
    const primary = { ...key, metaKey: mac, ctrlKey: !mac };
    expect(terminalShortcut({ ...primary, key: "f" }, mac)).toBe("find");
    expect(
      terminalShortcut({ ...primary, key: "C", shiftKey: !mac }, mac),
    ).toBe("copy");
    expect(
      terminalShortcut({ ...primary, key: "V", shiftKey: !mac }, mac),
    ).toBe("paste");
    expect(
      terminalShortcut(
        { ...primary, key: mac ? "[" : "PageUp", shiftKey: mac },
        mac,
      ),
    ).toBe("previous");
    expect(
      terminalShortcut(
        { ...primary, key: mac ? "]" : "PageDown", shiftKey: mac },
        mac,
      ),
    ).toBe("next");
    for (const letter of ["c", "d", "l", "r", "z"]) {
      expect(
        terminalShortcut({ ...key, key: letter, ctrlKey: true }, mac),
      ).toBeNull();
    }
    expect(
      terminalShortcut({ ...primary, key: "f", altKey: true }, mac),
    ).toBeNull();
    expect(
      terminalShortcut({ ...primary, key: "f", isComposing: true }, mac),
    ).toBeNull();
    expect(
      terminalShortcut(
        { ...primary, key: "f", ctrlKey: true, metaKey: true },
        mac,
      ),
    ).toBeNull();
    expect(terminalShortcutLabels(mac).copy).toBe(
      mac ? "Cmd+C" : "Ctrl+Shift+C",
    );
  },
);
