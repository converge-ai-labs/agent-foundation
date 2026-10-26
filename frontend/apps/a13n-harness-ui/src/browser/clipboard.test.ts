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
