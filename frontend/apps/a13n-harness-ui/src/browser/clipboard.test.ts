// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi, type Mock } from "vitest";
import { bindClipboard } from "./clipboard";

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
    expect(notice).toHaveBeenCalledWith(
      expect.stringContaining("Allow clipboard access"),
    ),
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

it.each([false, true])(
  "waits for native Unicode clipboard writes before paste (revoked=%s)",
  async (revoked) => {
    bridge.stop();
    let complete!: (text: string) => void;
    const pending = new Promise<string>((resolve) => {
      complete = resolve;
    });
    const nativeClipboard = vi.fn(() => pending);
    bridge = bindClipboard({
      root,
      active: () => controller,
      connection,
      shortcut,
      notice,
      nativeClipboard,
    });
    bridge.pasteText("你好世界 🚀");
    await vi.waitFor(() =>
      expect(nativeClipboard).toHaveBeenCalledWith("你好世界 🚀"),
    );
    expect(shortcut).not.toHaveBeenCalled();
    expect(connection.clipboardPasteFrom).not.toHaveBeenCalled();
    if (revoked) {
      controller = false;
      bridge.stop();
    }
    complete("你好世界 🚀");
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(shortcut).toHaveBeenCalledTimes(revoked ? 0 : 1);
  },
);

it.each([false, true])(
  "reads native UTF-8 on a legacy clipboard notification (revoked=%s)",
  async (revoked) => {
    bridge.stop();
    let complete!: (text: string) => void;
    const pending = new Promise<string>((resolve) => {
      complete = resolve;
    });
    const nativeClipboard = vi.fn(() => pending);
    bridge = bindClipboard({
      root,
      active: () => controller,
      connection,
      shortcut,
      notice,
      nativeClipboard,
    });
    receive("????");
    await vi.waitFor(() => expect(nativeClipboard).toHaveBeenCalledWith());
    if (revoked) {
      controller = false;
      bridge.stop();
    }
    complete("远端中文 🚀");
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (revoked) expect(writeText).not.toHaveBeenCalled();
    else expect(writeText).toHaveBeenCalledWith("远端中文 🚀");
  },
);

it("detects native Unicode changes without an RFB notification", async () => {
  bridge.stop();
  let remote = "";
  const nativeClipboard = vi.fn(async (text?: string) => {
    if (text !== undefined) remote = text;
    return remote;
  });
  bridge = bindClipboard({
    root,
    active: () => controller,
    connection,
    shortcut,
    notice,
    nativeClipboard,
  });
  bridge.sync();
  await vi.waitFor(() => expect(remote).toBe(local));
  remote = "中文";
  await vi.waitFor(() => {
    bridge.sync();
    expect(writeText).toHaveBeenCalledWith("中文");
  });
  remote = "另一段中文";
  await vi.waitFor(() => {
    bridge.sync();
    expect(writeText).toHaveBeenCalledWith("另一段中文");
  });
  controller = false;
  nativeClipboard.mockClear();
  bridge.sync();
  expect(nativeClipboard).not.toHaveBeenCalled();
});
