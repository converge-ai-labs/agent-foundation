type KeyEvent = Pick<
  KeyboardEvent,
  "key" | "code" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey" | "isComposing"
>;

export function isMacTerminal() {
  return /Mac|iPhone|iPad/.test(navigator.platform);
}

export function terminalShortcut(event: KeyEvent, mac = isMacTerminal()) {
  if (event.isComposing || event.altKey) return null;
  if (
    event.ctrlKey &&
    !event.metaKey &&
    (event.code === "Backquote" || event.key === "`" || event.key === "~")
  )
    return event.shiftKey ? "create" : "toggle";
  const primary = mac
    ? event.metaKey && !event.ctrlKey
    : event.ctrlKey && !event.metaKey;
  if (!primary) return null;
  const key = event.key.toLowerCase();
  if (key === "f" && !event.shiftKey) return "find";
  if (event.shiftKey === !mac) {
    if (key === "c") return "copy";
    if (key === "v") return "paste";
  }
  if (mac && event.shiftKey) {
    if (event.code === "BracketLeft" || key === "[") return "previous";
    if (event.code === "BracketRight" || key === "]") return "next";
  }
  if (!mac && !event.shiftKey) {
    if (key === "pageup") return "previous";
    if (key === "pagedown") return "next";
  }
  return null;
}

export function terminalShortcutLabels(mac = isMacTerminal()) {
  return {
    toggle: "Ctrl+`",
    create: "Ctrl+Shift+`",
    find: mac ? "Cmd+F" : "Ctrl+F",
    copy: mac ? "Cmd+C" : "Ctrl+Shift+C",
    paste: mac ? "Cmd+V" : "Ctrl+Shift+V",
    previous: mac ? "Cmd+Shift+[" : "Ctrl+PageUp",
    next: mac ? "Cmd+Shift+]" : "Ctrl+PageDown",
  };
}
