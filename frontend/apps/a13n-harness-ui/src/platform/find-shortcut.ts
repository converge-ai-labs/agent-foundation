import { usesCommandKey } from "./index";

export function isFindShortcut(
  event: Pick<
    KeyboardEvent,
    "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey" | "isComposing"
  >,
  command = usesCommandKey(),
) {
  return (
    !event.isComposing &&
    !event.altKey &&
    !event.shiftKey &&
    (command
      ? event.metaKey && !event.ctrlKey
      : event.ctrlKey && !event.metaKey) &&
    event.key.toLowerCase() === "f"
  );
}
