import type RFB from "@novnc/novnc";

/** Adapt viewer shortcuts to the Linux desktop; noVNC owns ordinary keys. */
export function bindKeyboard({
  root,
  active,
  connection,
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<RFB, "sendKey">;
}) {
  const mac = /mac|ipad|iphone|ipod/i.test(navigator.platform);
  const commands = new Set<string>();
  const controls = new Set<string>();
  const sendControl = (down: boolean) =>
    connection.sendKey(0xffe3, "ControlLeft", down);
  const release = () => {
    if (commands.size) sendControl(false);
    commands.clear();
    controls.clear();
  };
  const key = (event: KeyboardEvent) => {
    const down = event.type === "keydown";
    if (mac && event.key === "Meta") {
      // noVNC maps Command to Alt by default. Stop that mapping before its
      // canvas listener, so Command+A/C/V and browser shortcuts use Ctrl.
      if (down && !active()) return;
      event.preventDefault();
      event.stopPropagation();
      if (down) {
        if (!commands.size) sendControl(true);
        commands.add(event.code);
      } else {
        commands.delete(event.code);
        if (!commands.size && !controls.has("ControlLeft")) sendControl(false);
      }
    } else if (event.key === "Control") {
      if (down) controls.add(event.code);
      else controls.delete(event.code);
      // Let noVNC keep its platform-specific Ctrl/AltGr handling.
    }
  };
  const focusout = (event: FocusEvent) => {
    if (
      !(event.relatedTarget instanceof Node) ||
      !root.contains(event.relatedTarget)
    )
      release();
  };
  const visibility = () => {
    if (document.visibilityState !== "visible") release();
  };
  root.addEventListener("keydown", key, true);
  root.addEventListener("keyup", key, true);
  root.addEventListener("focusout", focusout);
  window.addEventListener("blur", release);
  document.addEventListener("visibilitychange", visibility);
  return {
    shortcut: (action: "copy" | "cut" | "paste") => {
      if (!active()) return;
      const letter = { copy: "c", cut: "x", paste: "v" }[action];
      sendControl(true);
      connection.sendKey(letter.charCodeAt(0), `Key${letter.toUpperCase()}`);
      if (!commands.size && !controls.has("ControlLeft")) sendControl(false);
    },
    stop: () => {
      release();
      root.removeEventListener("keydown", key, true);
      root.removeEventListener("keyup", key, true);
      root.removeEventListener("focusout", focusout);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", visibility);
    },
  };
}
