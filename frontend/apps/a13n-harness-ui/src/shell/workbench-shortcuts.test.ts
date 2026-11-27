// @vitest-environment jsdom
import { expect, it } from "vitest";
import {
  isNativeInputTarget,
  matchesShortcut,
  shortcutAttributes,
  shortcutChoices,
  shortcutDefaults,
  shortcutLabel,
} from "./workbench-shortcuts";

function key(init: KeyboardEventInit, prevented = false) {
  const event = new KeyboardEvent("keydown", { cancelable: true, ...init });
  if (prevented) event.preventDefault();
  return event;
}

it("keeps existing search and terminal defaults, with the new actions off", () => {
  expect(shortcutDefaults).toEqual({
    new_chat: "off",
    search_chats: "mod-f",
    toggle_sidebar: "off",
    toggle_supporting_panel: "off",
    open_files: "off",
    open_changes: "off",
    terminal: "ctrl-backtick",
    open_settings: "off",
  });
  for (const choices of Object.values(shortcutChoices)) {
    for (const choice of choices) {
      expect(shortcutLabel(choice, false) !== "").toBe(choice !== "off");
    }
  }
});

it.each([
  ["mod-f", "F", false],
  ["mod-k", "K", false],
  ["mod-shift-o", "O", true],
  ["mod-n", "N", false],
  ["mod-b", "B", false],
  ["mod-shift-s", "S", true],
  ["mod-j", "J", false],
  ["mod-shift-e", "E", true],
  ["mod-shift-g", "G", true],
  ["mod-comma", ",", false],
] as const)(
  "matches %s only with the correct key and modifiers",
  (choice, character, shiftKey) => {
    for (const command of [false, true]) {
      const init = {
        key: character.toLowerCase(),
        shiftKey,
        ctrlKey: !command,
        metaKey: command,
      };
      expect(matchesShortcut(key(init), choice, command)).toBe(true);
      expect(matchesShortcut(key(init), choice, !command)).toBe(false);
      expect(
        matchesShortcut(key({ ...init, shiftKey: !shiftKey }), choice, command),
      ).toBe(false);
      expect(
        matchesShortcut(
          key({ ...init, ctrlKey: true, metaKey: true }),
          choice,
          command,
        ),
      ).toBe(false);
      expect(
        matchesShortcut(key({ ...init, altKey: true }), choice, command),
      ).toBe(false);
    }
  },
);

it("supports both search keys and ignores canceled, repeating and composing events", () => {
  for (const character of ["f", "k"]) {
    const init = { key: character, ctrlKey: true };
    expect(matchesShortcut(key(init), "both", false)).toBe(true);
    expect(matchesShortcut(key(init), "off", false)).toBe(false);
    expect(matchesShortcut(key(init, true), "both", false)).toBe(false);
    expect(matchesShortcut(key({ ...init, repeat: true }), "both", false)).toBe(
      false,
    );
    expect(
      matchesShortcut(key({ ...init, isComposing: true }), "both", false),
    ).toBe(false);
  }
  expect(
    matchesShortcut(key({ key: "k", ctrlKey: true }), "mod-f", false),
  ).toBe(false);
});

it("describes the configured chords using platform labels and ARIA syntax", () => {
  expect(shortcutAttributes("New", "mod-shift-o", true)).toEqual({
    title: "New (⌘⇧O)",
    "aria-keyshortcuts": "Meta+Shift+O",
  });
  expect(shortcutAttributes("Search", "both", false)).toEqual({
    title: "Search (Ctrl+F / Ctrl+K)",
    "aria-keyshortcuts": "Control+F Control+K",
  });
  expect(shortcutAttributes("New", "off", true)).toEqual({
    title: "New",
    "aria-keyshortcuts": undefined,
  });
  expect(shortcutLabel("ctrl-backtick", true)).toBe("Ctrl+` / Ctrl+Shift+`");
});

it("recognizes Browser and Terminal input targets but not ordinary editors", () => {
  const container = document.createElement("div");
  container.innerHTML =
    "<div data-browser-screen><canvas></canvas></div><div data-terminal-input><textarea></textarea></div><div data-native-find><input></div>";
  expect(isNativeInputTarget(container.querySelector("canvas"))).toBe(true);
  expect(isNativeInputTarget(container.querySelector("textarea"))).toBe(true);
  expect(isNativeInputTarget(container.querySelector("input"))).toBe(false);
  expect(isNativeInputTarget(window)).toBe(false);
});
