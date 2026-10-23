import type RFB from "@novnc/novnc";

const LIMIT = 65536;
const unavailable =
  "Clipboard sync unavailable. Allow clipboard access in your browser's site settings.";

/** Bridge the focused controller's OS clipboard to noVNC's native clipboard API. */
export function bindClipboard({
  root,
  active,
  connection,
  shortcut,
  notice,
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<
    RFB,
    "clipboardPasteFrom" | "addEventListener" | "removeEventListener"
  >;
  shortcut: (action: "copy" | "cut" | "paste") => void;
  notice: (message: string) => void;
}) {
  let stopped = false;
  let last: string | undefined;
  let revision = 0;
  let readBlocked = false;
  let queued = 0;
  let serial = Promise.resolve();
  const allowed = () =>
    !stopped &&
    active() &&
    document.visibilityState === "visible" &&
    document.hasFocus() &&
    root.contains(document.activeElement);
  const bounded = (text: string) => {
    if (text.length > LIMIT)
      throw new Error(
        "Clipboard text exceeds 64 Ki characters. Nothing was transferred.",
      );
    return text;
  };
  const enqueue = (operation: () => Promise<void>) => {
    queued++;
    serial = serial
      .then(async () => {
        if (allowed()) await operation();
      })
      .catch((error: unknown) => {
        if (allowed())
          notice(error instanceof Error ? error.message : unavailable);
      })
      .finally(() => {
        queued--;
      });
  };
  const readLocal = () => {
    // Invoke in the input event's activation scope. Permission may resolve after handback.
    try {
      return navigator.clipboard.readText().then(
        (text) => ({ text }),
        () => ({ text: undefined }),
      );
    } catch {
      return Promise.resolve({ text: undefined });
    }
  };
  const send = (text: string) => {
    connection.clipboardPasteFrom(bounded(text));
    last = text;
    notice("");
  };
  const sync = (gesture = false) => {
    // Browsers have no clipboard-change event. Poll only the local clipboard;
    // remote changes arrive through noVNC's clipboard event, without HTTP or sleeps.
    if (!allowed() || queued || (readBlocked && !gesture)) return;
    const before = revision;
    const local = readLocal();
    enqueue(async () => {
      const { text } = await local;
      if (!allowed() || before !== revision) return;
      readBlocked = text === undefined;
      if (text === undefined) notice(unavailable);
      else if (text !== last) send(text);
    });
  };
  const receive = (event: Event) => {
    if (!allowed()) return;
    const { text } = (event as CustomEvent<{ text: string }>).detail;
    revision++;
    enqueue(async () => {
      if (text === last) return;
      try {
        await navigator.clipboard.writeText(bounded(text));
      } catch {
        throw new Error(unavailable);
      }
      if (allowed()) {
        last = text;
        notice("");
      }
    });
  };
  const paste = (content: Promise<{ text: string | undefined }>) =>
    enqueue(async () => {
      const { text } = await content;
      if (!allowed()) return;
      if (text === undefined) throw new Error(unavailable);
      // Clipboard negotiation and keys share the ordered RFB transport.
      if (text !== last) send(text);
      shortcut("paste");
    });
  const keydown = (event: KeyboardEvent) => {
    if (
      !allowed() ||
      !(event.ctrlKey || event.metaKey) ||
      event.altKey ||
      event.key.toLowerCase() !== "v"
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (!event.repeat) paste(readLocal());
  };
  const onCopy = (event: ClipboardEvent) => {
    if (!allowed()) return;
    event.preventDefault();
    event.stopPropagation();
    if (last !== undefined) event.clipboardData?.setData("text/plain", last);
    shortcut(event.type === "cut" ? "cut" : "copy");
  };
  const onPaste = (event: ClipboardEvent) => {
    if (!allowed()) return;
    event.preventDefault();
    event.stopPropagation();
    if (!event.clipboardData?.types.includes("text/plain")) {
      notice("Only plain text clipboard transfer is supported.");
      return;
    }
    paste(Promise.resolve({ text: event.clipboardData.getData("text/plain") }));
  };
  const focus = () => sync();
  const pointer = () => sync(true);
  connection.addEventListener("clipboard", receive);
  root.addEventListener("keydown", keydown, true);
  root.addEventListener("paste", onPaste, true);
  root.addEventListener("copy", onCopy, true);
  root.addEventListener("cut", onCopy, true);
  root.addEventListener("pointerup", pointer);
  root.addEventListener("focusin", focus);
  window.addEventListener("focus", focus);
  document.addEventListener("visibilitychange", focus);
  const interval = setInterval(sync, 500);
  return {
    sync: focus,
    // IME commits reuse ordered clipboard transfer without writing the local
    // clipboard. The next sync restores its contents on the Host.
    pasteText: (text: string) => paste(Promise.resolve({ text })),
    stop: () => {
      stopped = true;
      last = undefined;
      clearInterval(interval);
      connection.removeEventListener("clipboard", receive);
      root.removeEventListener("keydown", keydown, true);
      root.removeEventListener("paste", onPaste, true);
      root.removeEventListener("copy", onCopy, true);
      root.removeEventListener("cut", onCopy, true);
      root.removeEventListener("pointerup", pointer);
      root.removeEventListener("focusin", focus);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    },
  };
}
