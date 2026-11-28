import type RFB from "@novnc/novnc";

class ClipboardMessage extends Error {}

const LIMIT = 65536;
const unavailable = "Clipboard sync is unavailable.";
const automaticCopyBlocked = "Automatic clipboard copy was blocked.";
const manualCopyFailed = "Could not copy remote text. Try again.";

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
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<
    RFB,
    "clipboardPasteFrom" | "addEventListener" | "removeEventListener"
  >;
  shortcut: (action: "copy" | "cut" | "paste") => void;
  notice: (
    message: string,
    external?: boolean,
    canCopyRemote?: boolean,
  ) => void;
}) {
  let stopped = false;
  let suspended = false;
  let generation = 0;
  let last: string | undefined;
  let revision = 0;
  let pending: { text: string; copying: boolean } | undefined;
  let readBlocked = false;
  let queued = 0;
  let serial = Promise.resolve();
  const inControl = () =>
    !stopped &&
    !suspended &&
    active() &&
    document.visibilityState === "visible";
  const allowed = () =>
    inControl() && document.hasFocus() && root.contains(document.activeElement);
  const clearPending = () => {
    if (!pending) return;
    pending = undefined;
    notice("");
  };
  const bounded = (text: string) => {
    if (text.length > LIMIT)
      throw new ClipboardMessage(
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
        if (current() && !pending) {
          if (error instanceof ClipboardMessage) notice(error.message);
          else if (error instanceof Error) notice(error.message, true);
          else notice(unavailable);
        }
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
    connection.clipboardPasteFrom(bounded(text));
    last = text;
    pending = undefined;
    notice("");
  };
  const sync = (gesture = false) => {
    // Browsers have no clipboard-change event.
    if (!allowed() || queued || (readBlocked && !gesture)) return;
    const before = revision;
    const local = readLocal();
    enqueue(async (current) => {
      const { text } = await local;
      if (!current() || before !== revision) return;
      readBlocked = text === undefined;
      if (text === undefined) {
        if (!pending) notice(unavailable);
      } else if (text !== last) await send(text);
    });
  };
  const writeLocal = async (text: string) => {
    const write = navigator.clipboard.writeText(text);
    // A submitted OS write cannot be cancelled. Screenshot copy waits for it.
    const settled = write.catch(() => {});
    pendingWrites.add(settled);
    void settled.then(() => pendingWrites.delete(settled));
    await write;
  };
  const receive = (event: Event) => {
    // A new remote transfer also replaces an offer while its button has focus.
    const receivedRevision = ++revision;
    clearPending();
    if (!allowed()) return;
    const epoch = generation;
    enqueue(async (current) => {
      const text = bounded(
        (event as CustomEvent<{ text: string }>).detail.text,
      );
      if (text === last) return;
      try {
        await writeLocal(text);
      } catch {
        if (
          epoch === generation &&
          inControl() &&
          receivedRevision === revision
        ) {
          pending = { text, copying: false };
          notice(automaticCopyBlocked, false, true);
        }
        return;
      }
      if (current()) {
        last = text;
        notice("");
      }
    });
  };
  const copyRemote = async () => {
    // The notice button is outside the VNC root. Start the write within the
    // click, without waiting for the bridge queue or a clipboard read.
    if (!inControl() || !pending || pending.copying) return;
    const offer = pending;
    offer.copying = true;
    revision++;
    const epoch = generation;
    const sameOffer = () => epoch === generation && pending === offer;
    try {
      await writeLocal(offer.text);
      if (sameOffer()) {
        last = offer.text;
        clearPending();
      }
    } catch {
      if (sameOffer() && inControl()) notice(manualCopyFailed, false, true);
    } finally {
      offer.copying = false;
    }
  };
  const paste = (content: Promise<{ text: string | undefined }>) =>
    enqueue(async (current) => {
      const { text } = await content;
      if (!current()) return;
      if (text === undefined) throw new ClipboardMessage(unavailable);
      // Send the clipboard contents before the VNC paste shortcut.
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
    copyRemote,
    suspend: () => {
      suspended = true;
      generation++;
      clearPending();
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
      clearPending();
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
