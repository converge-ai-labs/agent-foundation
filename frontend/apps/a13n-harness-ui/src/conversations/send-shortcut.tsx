import { createContext, useContext } from "react";
import { useMediaQuery } from "a13n-ui";
import { usesCommandKey } from "../platform";
import type { Schema } from "../transport/client";

export const SendShortcutContext =
  createContext<Schema<"AppStatus">["send_shortcut"]>("enter");

export function useComposerSendShortcut() {
  const preference = useContext(SendShortcutContext) ?? "enter";
  const touch = useMediaQuery("(pointer: coarse)");
  const plainEnterSends = preference === "enter" && !touch;
  const command = usesCommandKey();
  const modifiedEnter = `${command ? "Meta" : "Control"}+Enter`;
  return {
    plainEnterSends,
    command,
    label: plainEnterSends ? "Enter" : `${command ? "⌘" : "Ctrl"} + Enter`,
    ariaKeyShortcuts: plainEnterSends
      ? `Enter ${modifiedEnter}`
      : modifiedEnter,
  };
}
