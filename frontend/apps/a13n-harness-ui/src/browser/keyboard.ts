import type RFB from "@novnc/novnc";

/** Adapt modifiers to the Host desktop; noVNC owns ordinary keys. */
export function bindKeyboard({
  root,
  active,
  connection,
  targetMac = false,
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<RFB, "sendKey">;
  targetMac?: boolean;
}) {
  const mac = /mac|ipad|iphone|ipod/i.test(navigator.platform);
  const commands = new Set<string>();
  const controls = new Set<string>();
  const pressed = new Map<string, KeyboardEvent>();
  const options = new Map<string, number>();
  const sendPrimary = (down: boolean) =>
    connection.sendKey(
      targetMac ? 0xffe9 : 0xffe3,
      targetMac ? "MetaLeft" : "ControlLeft",
      down,
    );
  const release = () => {
    if (commands.size) sendPrimary(false);
    commands.clear();
    for (const [code, symbol] of options)
      connection.sendKey(symbol, code, false);
    options.clear();
    // Keyup follows focus, so noVNC's canvas can miss it. Release through
    // its own listener to settle AltGr detection and clear its key state too.
    for (const event of pressed.values())
      event.target?.dispatchEvent(new KeyboardEvent("keyup", event));
    pressed.clear();
    controls.clear();
  };
  const key = (event: KeyboardEvent) => {
    const down = event.type === "keydown";
    if (targetMac && event.key === "Alt") {
      if (down && !active()) return;
      event.preventDefault();
      event.stopPropagation();
      // Apple's RFB server uses Meta for Option and Alt for Command.
      const symbol = event.code === "AltRight" ? 0xffe8 : 0xffe7;
      connection.sendKey(symbol, event.code, down);
      if (down) options.set(event.code, symbol);
      else options.delete(event.code);
      return;
    }
    if ((mac || targetMac) && event.key === "Meta") {
      // noVNC maps a Mac client's Command to Alt. Select the Host's
      // native shortcut modifier before its canvas listener.
      if (down && !active()) return;
      event.preventDefault();
      event.stopPropagation();
      if (down) {
        if (!commands.size) sendPrimary(true);
        commands.add(event.code);
      } else {
        commands.delete(event.code);
        if (!commands.size && (targetMac || !controls.has("ControlLeft")))
          sendPrimary(false);
      }
    } else if (event.key === "Control") {
      if (down) controls.add(event.code);
      else controls.delete(event.code);
      // Let noVNC keep its platform-specific Ctrl/AltGr handling.
    }
    if (!((mac || targetMac) && event.key === "Meta")) {
      const code = event.code || event.key;
      if (down) pressed.set(code, event);
      else pressed.delete(code);
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
  root.addEventListener("compositionstart", release, true);
  window.addEventListener("blur", release);
  document.addEventListener("visibilitychange", visibility);
  return {
    shortcut: (action: "copy" | "cut" | "paste") => {
      if (!active()) return;
      const letter = { copy: "c", cut: "x", paste: "v" }[action];
      sendPrimary(true);
      connection.sendKey(letter.charCodeAt(0), `Key${letter.toUpperCase()}`);
      if (!commands.size && (targetMac || !controls.has("ControlLeft")))
        sendPrimary(false);
    },
    stop: () => {
      release();
      root.removeEventListener("keydown", key, true);
      root.removeEventListener("keyup", key, true);
      root.removeEventListener("focusout", focusout);
      root.removeEventListener("compositionstart", release, true);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", visibility);
    },
  };
}
