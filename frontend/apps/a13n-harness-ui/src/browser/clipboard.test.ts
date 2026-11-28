// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi, type Mock } from "vitest";
import { bindClipboard, waitForClipboardWrites } from "./clipboard";

let root: HTMLDivElement;
let controller: boolean;
let local: string;
let connection: EventTarget & {
  clipboardPasteFrom: Mock<(text: string) => void>;
};
let bridge: ReturnType<typeof bindClipboard>;
const readText = vi.fn();
const writeText = vi.fn();
const shortcut = vi.fn();
const notice = vi.fn();
const receive = (text: string) =>
  connection.dispatchEvent(new CustomEvent("clipboard", { detail: { text } }));
beforeEach(() => {
  vi.clearAllMocks();
  root = document.createElement("div");
  root.tabIndex = 0;
  document.body.append(root);
  root.focus();
  controller = true;
  local = "本地 clipboard 🙂";
  connection = Object.assign(new EventTarget(), {
    clipboardPasteFrom: vi.fn(),
  });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  readText.mockImplementation(async () => local);
  writeText.mockImplementation(async (text: string) => {
    local = text;
  });
  vi.stubGlobal("navigator", { clipboard: { readText, writeText } });
  bridge = bindClipboard({
    root,
    active: () => controller,
    connection,
    shortcut,
    notice,
  });
});
afterEach(() => {
  bridge.stop();
  root.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("uses noVNC's Unicode API and clipboard events without echoing remote copies", async () => {
  bridge.sync();
  await vi.waitFor(() =>
    expect(connection.clipboardPasteFrom).toHaveBeenCalledWith(local),
  );
  receive("远端 copied ✨");
  await vi.waitFor(() =>
    expect(writeText).toHaveBeenCalledWith("远端 copied ✨"),
  );
  connection.clipboardPasteFrom.mockClear();
  bridge.sync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
});

it("commits IME text without changing the local clipboard and restores the Host on sync", async () => {
  bridge.sync();
  await vi.waitFor(() =>
    expect(connection.clipboardPasteFrom).toHaveBeenCalledWith(local),
  );
  bridge.pasteText("你好世界 🚀");
  await vi.waitFor(() => expect(shortcut).toHaveBeenCalledWith("paste"));
  expect(connection.clipboardPasteFrom).toHaveBeenLastCalledWith("你好世界 🚀");
  receive("你好世界 🚀");
  await Promise.resolve();
  expect(writeText).not.toHaveBeenCalled();
  expect(local).toBe("本地 clipboard 🙂");
  await vi.waitFor(() => {
    bridge.sync();
    expect(connection.clipboardPasteFrom).toHaveBeenLastCalledWith(local);
  });
});
it("never reads or writes clipboard contents without current focused input authority", async () => {
  controller = false;
  bridge.sync();
  receive("must stay remote");
  root.dispatchEvent(
    new KeyboardEvent("keydown", { key: "v", ctrlKey: true, bubbles: true }),
  );
  await Promise.resolve();
  expect(readText).not.toHaveBeenCalled();
  expect(writeText).not.toHaveBeenCalled();
  expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
  expect(shortcut).not.toHaveBeenCalled();
  controller = true;
  root.blur();
  bridge.sync();
  receive("unfocused");
  expect(readText).not.toHaveBeenCalled();
  expect(writeText).not.toHaveBeenCalled();
});
it("discards permission-delayed reads after handback or disconnection", async () => {
  let complete!: (text: string) => void;
  readText.mockReturnValue(
    new Promise<string>((resolve) => {
      complete = resolve;
    }),
  );
  bridge.sync();
  controller = false;
  bridge.stop();
  complete("must stay local");
  receive("must stay remote");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
  expect(writeText).not.toHaveBeenCalled();
});
it("does not overwrite a newly received remote copy with an older in-flight local read", async () => {
  let complete!: (text: string) => void;
  readText.mockReturnValue(
    new Promise<string>((resolve) => {
      complete = resolve;
    }),
  );
  bridge.sync();
  receive("new remote");
  complete("stale local");
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("new remote"));
  expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
});
it("puts clipboard negotiation before the paste shortcut on the same RFB connection", async () => {
  root.dispatchEvent(
    new KeyboardEvent("keydown", {
      key: "v",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }),
  );
  await vi.waitFor(() => expect(shortcut).toHaveBeenCalledWith("paste"));
  expect(connection.clipboardPasteFrom).toHaveBeenCalledWith(local);
  expect(
    connection.clipboardPasteFrom.mock.invocationCallOrder[0],
  ).toBeLessThan(shortcut.mock.invocationCallOrder[0]);
});
it("leaves select-all and copy keydowns to noVNC and supports native copy menu events", () => {
  const listener = vi.fn();
  root.addEventListener("keydown", listener);
  for (const key of ["a", "c", "x", "z"])
    root.dispatchEvent(
      new KeyboardEvent("keydown", {
        key,
        metaKey: true,
        bubbles: true,
        cancelable: true,
      }),
    );
  expect(listener).toHaveBeenCalledTimes(4);
  expect(shortcut).not.toHaveBeenCalled();
  root.dispatchEvent(new Event("copy", { bubbles: true, cancelable: true }));
  expect(shortcut).toHaveBeenCalledWith("copy");
});
it("keeps the remote selection owner when pasting an unchanged clipboard", async () => {
  bridge.sync();
  await vi.waitFor(() =>
    expect(connection.clipboardPasteFrom).toHaveBeenCalledOnce(),
  );
  root.dispatchEvent(
    new KeyboardEvent("keydown", {
      key: "v",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }),
  );
  await vi.waitFor(() => expect(shortcut).toHaveBeenCalledWith("paste"));
  expect(connection.clipboardPasteFrom).toHaveBeenCalledOnce();
});
it("stops polling denied reads and retries on a direct interaction", async () => {
  readText.mockRejectedValue(new DOMException("blocked", "NotAllowedError"));
  bridge.sync();
  await vi.waitFor(() =>
    expect(notice).toHaveBeenCalledWith("Clipboard sync is unavailable."),
  );
  bridge.sync();
  expect(readText).toHaveBeenCalledOnce();
  readText.mockResolvedValue("now allowed");
  root.dispatchEvent(new Event("pointerup", { bubbles: true }));
  await vi.waitFor(() =>
    expect(connection.clipboardPasteFrom).toHaveBeenCalledWith("now allowed"),
  );
});
it("does not truncate or partially transfer an oversized clipboard", async () => {
  local = "x".repeat(65537);
  bridge.sync();
  await vi.waitFor(() =>
    expect(notice).toHaveBeenCalledWith(expect.stringContaining("exceeds")),
  );
  expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
});

it("suspends queued transfers and waits only for an already-issued OS write", async () => {
  let finish!: () => void;
  writeText.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve;
    }),
  );
  receive("in flight");
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledOnce());
  receive("queued");
  const settled = vi.fn();
  const ready = bridge.suspend().then(settled);
  bridge.sync();
  receive("while editing");
  await Promise.resolve();
  expect(settled).not.toHaveBeenCalled();
  finish();
  await ready;
  bridge.resume();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(writeText).toHaveBeenCalledOnce();
  expect(readText).not.toHaveBeenCalled();
});
it.each(["sync", "paste"])(
  "invalidates delayed %s across a suspend/resume and allows fresh reads",
  async (operation) => {
    let finish!: (text: string) => void;
    readText.mockReturnValueOnce(
      new Promise<string>((resolve) => {
        finish = resolve;
      }),
    );
    if (operation === "sync") bridge.sync();
    else
      root.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "v",
          ctrlKey: true,
          bubbles: true,
        }),
      );
    await Promise.resolve();
    await bridge.suspend();
    bridge.resume();
    bridge.sync();
    await vi.waitFor(() =>
      expect(connection.clipboardPasteFrom).toHaveBeenCalledWith(local),
    );
    finish("stale permission result");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(connection.clipboardPasteFrom).toHaveBeenCalledOnce();
    expect(shortcut).not.toHaveBeenCalled();
  },
);

it.each(["reconnect", "cancel and reopen"])(
  "waits for every issued write across %s before copying a screenshot",
  async (transition) => {
    const finishes: (() => void)[] = [];
    writeText.mockImplementation(
      (text: string) =>
        new Promise<void>((resolve) => {
          finishes.push(() => {
            local = text;
            resolve();
          });
        }),
    );
    receive("first write");
    await vi.waitFor(() => expect(finishes).toHaveLength(1));
    if (transition === "reconnect") {
      bridge.stop();
      bridge = bindClipboard({
        root,
        active: () => controller,
        connection,
        shortcut,
        notice,
      });
    } else {
      void bridge.suspend();
      bridge.resume();
    }
    receive("second write");
    await vi.waitFor(() => expect(finishes).toHaveLength(2));
    const copied = vi.fn(() => {
      local = "PNG";
    });
    const ready = bridge.suspend().then(copied);
    finishes[1]();
    // Drain the settled write and all barrier continuations, without a timer.
    for (let i = 0; i < 10; i++) await Promise.resolve();
    const copiedEarly = copied.mock.calls.length;
    finishes[0]();
    await ready;
    expect(copiedEarly).toBe(0);
    expect(local).toBe("PNG");
  },
);

async function offerRemote(text: string) {
  writeText.mockRejectedValueOnce(
    new DOMException("blocked", "NotAllowedError"),
  );
  receive(text);
  await vi.waitFor(() =>
    expect(notice).toHaveBeenLastCalledWith(
      "Automatic clipboard copy was blocked.",
      false,
      true,
    ),
  );
}

it("starts the manual write immediately even when the copy button has focus", async () => {
  const text = "界".repeat(65536);
  await offerRemote(text);
  const button = document.createElement("button");
  document.body.append(button);
  button.focus();
  try {
    expect(root.contains(document.activeElement)).toBe(false);
    const copy = bridge.copyRemote();
    expect(writeText).toHaveBeenLastCalledWith(text);
    expect(writeText).toHaveBeenCalledTimes(2);
    await copy;
    expect(local).toBe(text);
    expect(notice).toHaveBeenLastCalledWith("");
    await bridge.copyRemote();
    expect(writeText).toHaveBeenCalledTimes(2);
  } finally {
    button.remove();
  }
});

it("lets the user retry a rejected manual copy during the same control episode", async () => {
  await offerRemote("remote text");
  writeText.mockRejectedValueOnce(
    new DOMException("blocked", "NotAllowedError"),
  );
  await bridge.copyRemote();
  expect(notice).toHaveBeenLastCalledWith(
    "Could not copy remote text. Try again.",
    false,
    true,
  );
  await bridge.copyRemote();
  expect(local).toBe("remote text");
  expect(notice).toHaveBeenLastCalledWith("");
});

it.each(["suspend", "disconnect", "handback", "local sync", "remote update"])(
  "does not copy an old offer after %s",
  async (transition) => {
    await offerRemote("old remote text");
    if (transition === "suspend") {
      await bridge.suspend();
      bridge.resume();
    } else if (transition === "disconnect" || transition === "handback") {
      controller = false;
      await bridge.copyRemote();
      bridge.stop();
    } else if (transition === "local sync") {
      local = "new local selection";
      bridge.sync();
      await vi.waitFor(() =>
        expect(connection.clipboardPasteFrom).toHaveBeenCalledWith(local),
      );
    } else {
      // A remote update still invalidates the old offer while the notice has focus.
      root.blur();
      receive("new remote selection");
    }
    expect(notice).toHaveBeenLastCalledWith("");
    await bridge.copyRemote();
    expect(writeText).toHaveBeenCalledOnce();
  },
);

it("does not offer or write oversized remote text", async () => {
  receive("界".repeat(65537));
  await vi.waitFor(() =>
    expect(notice).toHaveBeenLastCalledWith(expect.stringContaining("exceeds")),
  );
  await bridge.copyRemote();
  expect(writeText).not.toHaveBeenCalled();
});

it("keeps an issued manual copy in the screenshot barrier after suspend", async () => {
  await offerRemote("remote text");
  let settle!: () => void;
  writeText.mockReturnValueOnce(
    new Promise<void>((resolve) => {
      settle = resolve;
    }),
  );
  const copy = bridge.copyRemote();
  void bridge.copyRemote();
  expect(writeText).toHaveBeenCalledTimes(2);
  const ready = vi.fn();
  const barrier = bridge.suspend().then(ready);
  await Promise.resolve();
  expect(ready).not.toHaveBeenCalled();
  expect(notice).toHaveBeenLastCalledWith("");
  settle();
  await Promise.all([copy, barrier, waitForClipboardWrites()]);
  expect(ready).toHaveBeenCalledOnce();
  expect(notice).toHaveBeenLastCalledWith("");
});

it("never revives an offer from a denied automatic write after reactivating", async () => {
  let reject!: (error: Error) => void;
  writeText.mockReturnValueOnce(
    new Promise<void>((_, fail) => {
      reject = fail;
    }),
  );
  receive("old remote text");
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledOnce());
  const barrier = bridge.suspend();
  bridge.resume();
  reject(new DOMException("blocked", "NotAllowedError"));
  await barrier;
  await bridge.copyRemote();
  expect(notice).not.toHaveBeenCalledWith(expect.any(String), false, true);
  expect(writeText).toHaveBeenCalledOnce();
});
