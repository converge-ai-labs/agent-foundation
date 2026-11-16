import { expect, it } from "vitest";
import { isFindShortcut } from "./find-shortcut";

const key = {
  key: "f",
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  isComposing: false,
};

it.each([true, false])(
  "matches only plain platform Find (Command: %s)",
  (command) => {
    const find = { ...key, metaKey: command, ctrlKey: !command };
    expect(isFindShortcut(find, command)).toBe(true);
    expect(isFindShortcut({ ...find, key: "F" }, command)).toBe(true);
    for (const patch of [
      { altKey: true },
      { shiftKey: true },
      { isComposing: true },
      { ctrlKey: true, metaKey: true },
      { key: "g" },
      { ctrlKey: false, metaKey: false },
      { ctrlKey: command, metaKey: !command },
    ]) {
      expect(isFindShortcut({ ...find, ...patch }, command)).toBe(false);
    }
  },
);
