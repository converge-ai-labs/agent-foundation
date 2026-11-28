import { createContext, useContext, useEffect, useEffectEvent } from "react";
import { usesCommandKey } from "../platform";
import type { Schema } from "../transport/client";

type ShortcutsConfiguration = Schema<"WebUiShortcutsConfiguration">;
// Only the declared shortcuts are interpreted; configuration retains additive fields.
export type WorkbenchShortcuts = {
  [
    K in keyof ShortcutsConfiguration as string extends K ? never : K
  ]-?: ShortcutsConfiguration[K];
};
export type ShortcutAction = keyof WorkbenchShortcuts;
type ShortcutChoice = WorkbenchShortcuts[ShortcutAction];

export const shortcutDefaults: WorkbenchShortcuts = {
  new_chat: "off",
  search_chats: "mod-f",
  toggle_sidebar: "off",
  toggle_supporting_panel: "off",
  open_files: "off",
  open_changes: "off",
  terminal: "ctrl-backtick",
  open_settings: "off",
};

export const shortcutChoices = {
  new_chat: ["off", "mod-shift-o", "mod-n"],
  search_chats: ["mod-f", "mod-k", "both", "off"],
  toggle_sidebar: ["off", "mod-b", "mod-shift-s"],
  toggle_supporting_panel: ["off", "mod-j"],
  open_files: ["off", "mod-shift-e"],
  open_changes: ["off", "mod-shift-g"],
  terminal: ["ctrl-backtick", "off"],
  open_settings: ["off", "mod-comma"],
} as const satisfies {
  [A in ShortcutAction]: readonly WorkbenchShortcuts[A][];
};

export const WorkbenchShortcutsContext = createContext<
  Partial<WorkbenchShortcuts> | undefined
>(undefined);

export function useWorkbenchShortcut<A extends ShortcutAction>(
  action: A,
): WorkbenchShortcuts[A] {
  return (
    useContext(WorkbenchShortcutsContext)?.[action] ?? shortcutDefaults[action]
  );
}

const chords = {
  "mod-f": ["F", false],
  "mod-k": ["K", false],
  "mod-shift-o": ["O", true],
  "mod-n": ["N", false],
  "mod-b": ["B", false],
  "mod-shift-s": ["S", true],
  "mod-j": ["J", false],
  "mod-shift-e": ["E", true],
  "mod-shift-g": ["G", true],
  "mod-comma": [",", false],
  "ctrl-backtick": ["`", false],
} as const satisfies Record<
  Exclude<ShortcutChoice, "off" | "both">,
  readonly [string, boolean]
>;

function shortcutKeys(choice: ShortcutChoice) {
  if (choice === "off") return [];
  if (choice === "both") return ["mod-f", "mod-k"] as const;
  return [choice];
}

function keyCombinations(choice: ShortcutChoice, command: boolean) {
  return shortcutKeys(choice).flatMap((key) => {
    const [character, shift] = chords[key];
    return key === "ctrl-backtick"
      ? [
          { character, shift: false, meta: false },
          { character, shift: true, meta: false },
        ]
      : [{ character, shift, meta: command }];
  });
}

export function shortcutLabel(
  choice: ShortcutChoice,
  command = usesCommandKey(),
) {
  return keyCombinations(choice, command)
    .map(({ character, shift, meta }) =>
      meta
        ? `⌘${shift ? "⇧" : ""}${character}`
        : `Ctrl+${shift ? "Shift+" : ""}${character}`,
    )
    .join(" / ");
}

export function shortcutAttributes(
  label: string,
  choice: ShortcutChoice,
  command = usesCommandKey(),
) {
  const display = shortcutLabel(choice, command);
  const keys = keyCombinations(choice, command)
    .map(
      ({ character, shift, meta }) =>
        `${meta ? "Meta" : "Control"}+${shift ? "Shift+" : ""}${character}`,
    )
    .join(" ");
  return {
    title: display ? `${label} (${display})` : label,
    "aria-keyshortcuts": keys || undefined,
  };
}

type ShortcutEvent = Pick<
  KeyboardEvent,
  | "key"
  | "ctrlKey"
  | "metaKey"
  | "altKey"
  | "shiftKey"
  | "defaultPrevented"
  | "isComposing"
  | "repeat"
>;

export function ignoreShortcut(event: ShortcutEvent) {
  return event.defaultPrevented || event.isComposing || event.repeat;
}

export function matchesShortcut(
  event: ShortcutEvent,
  choice: Exclude<ShortcutChoice, "ctrl-backtick">,
  command = usesCommandKey(),
) {
  return (
    !ignoreShortcut(event) &&
    !event.altKey &&
    keyCombinations(choice, command).some(
      ({ character, shift, meta }) =>
        event.metaKey === meta &&
        event.ctrlKey === !meta &&
        event.shiftKey === shift &&
        event.key.toUpperCase() === character,
    )
  );
}

export function isNativeInputTarget(target: EventTarget | null) {
  return (
    target instanceof Element &&
    !!target.closest("[data-browser-screen], [data-terminal-input]")
  );
}

export function useWorkbenchKeydown(
  handler: (event: KeyboardEvent) => void,
  capture = false,
) {
  const onKeydown = useEffectEvent(handler);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKeydown(event);
    window.addEventListener("keydown", listener, capture);
    return () => window.removeEventListener("keydown", listener, capture);
  }, [capture]);
}
