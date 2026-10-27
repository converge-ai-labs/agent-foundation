import type RFB from "@novnc/novnc";

const LIMIT = 65536;
const unavailable =
  "Clipboard sync unavailable. Allow clipboard access in your browser's site settings.";

// The local clipboard outlives individual connections and mounted viewers.
// Keep every submitted write until settlement, including stopped bridges.
const pendingWrites = new Set<Promise<void>>();
export async function waitForClipboardWrites(): Promise<void> {
  await Promise.all(pendingWrites);
}

/** Bridge the focused controller's OS clipboard to noVNC's native clipboard API. */
export function bindClipboard({
  root,
  active,
  connection,
  shortcut,
  notice,
  nativeClipboard,
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<
    RFB,
    "clipboardPasteFrom" | "addEventListener" | "removeEventListener"
  >;
  shortcut: (action: "copy" | "cut" | "paste") => void;
  notice: (message: string) => void;
  nativeClipboard?: (text?: string) => Promise<string>;
}) {
  let stopped = false;
  let suspended = false;
  let generation = 0;
  let last: string | undefined;
  let revision = 0;
  let readBlocked = false;
  let queued = 0;
  let serial = Promise.resolve();
  const allowed = () =>
    !stopped &&
    !suspended &&
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
  const enqueue = (operation: (current: () => boolean) => Promise<void>) => {
    const epoch = generation;
    const current = () => epoch === generation && allowed();
    queued++;
    serial = serial
      .then(async () => {
        if (current()) await operation(current);
      })
      .catch((error: unknown) => {
        if (current())
          notice(error instanceof Error ? error.message : unavailable);
      })
      .finally(() => {
        if (epoch === generation) queued--;
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
  const send = async (text: string) => {
    if (nativeClipboard) await nativeClipboard(bounded(text));
    else connection.clipboardPasteFrom(bounded(text));
    last = text;
    notice("");
  };
  const sync = (gesture = false) => {
    // Browsers have no clipboard-change event. macOS also needs a native read:
    // Apple's legacy RFB notifications cannot represent every Unicode change.
    if (!allowed() || queued || (readBlocked && !gesture)) return;
    const before = revision;
    const local = readLocal();
    enqueue(async (current) => {
      const { text } = await local;
      if (!current() || before !== revision) return;
      readBlocked = text === undefined;
      if (text === undefined) notice(unavailable);
      else if (text !== last) await send(text);
      else if (nativeClipboard)
        await writeLocal(await nativeClipboard(), current);
    });
  };
  const writeLocal = async (text: string, current: () => boolean) => {
    if (!current() || text === last) return;
    try {
      const write = navigator.clipboard.writeText(bounded(text));
      // A submitted OS write cannot be cancelled. Screenshot copy waits for it.
      const settled = write.catch(() => {});
      pendingWrites.add(settled);
      void settled.then(() => pendingWrites.delete(settled));
      await write;
    } catch {
      throw new Error(unavailable);
    }
    if (current()) {
      last = text;
      notice("");
    }
  };
  const receive = (event: Event) => {
    if (!allowed()) return;
    const received = (event as CustomEvent<{ text: string }>).detail.text;
    revision++;
    enqueue(async (current) => {
      const text = nativeClipboard ? await nativeClipboard() : received;
      await writeLocal(text, current);
    });
  };
  const paste = (content: Promise<{ text: string | undefined }>) =>
    enqueue(async (current) => {
      const { text } = await content;
      if (!current()) return;
      if (text === undefined) throw new Error(unavailable);
      // Native clipboard writes must settle before the VNC paste shortcut.
      if (text !== last) await send(text);
      if (current()) shortcut("paste");
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
    suspend: () => {
      suspended = true;
      generation++;
      // Permission reads may stay pending indefinitely; they must not block copy.
      serial = Promise.resolve();
      queued = 0;
      return waitForClipboardWrites();
    },
    resume: () => {
      suspended = false;
    },
    // IME commits reuse ordered clipboard transfer without writing the local
    // clipboard. The next sync restores its contents on the Host.
    pasteText: (text: string) => paste(Promise.resolve({ text })),
    stop: () => {
      stopped = true;
      generation++;
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
