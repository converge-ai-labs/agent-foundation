// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ChatSearch, ChatSearchProvider } from "./chat-search";
import {
  WorkbenchShortcutsContext,
  type WorkbenchShortcuts,
} from "../shell/workbench-shortcuts";

vi.mock("../transport/context", () => ({
  useProjects: () => ({ data: [] }),
}));
vi.mock("./queries", () => ({
  useThreads: () => ({ data: { pages: [] }, isSuccess: true }),
}));
afterEach(cleanup);

function fixture(shortcut?: WorkbenchShortcuts["search_chats"]) {
  const opened = vi.fn();
  const view = render(
    <WorkbenchShortcutsContext value={{ search_chats: shortcut }}>
      <ChatSearchProvider>
        <textarea aria-label="Message" />
        <ChatSearch onOpen={opened} />
        <div hidden>
          <ChatSearch onOpen={() => {}} />
        </div>
        <div data-native-find="">
          <button>File preview</button>
        </div>
        <div data-terminal-input="">
          <button>Terminal input</button>
        </div>
        <div data-browser-screen="">
          <button>Browser input</button>
        </div>
      </ChatSearchProvider>
    </WorkbenchShortcutsContext>,
  );
  const message = screen.getByRole("textbox", { name: "Message" });
  message.focus();
  return { ...view, message, opened };
}

it("opens one search from the composer, refocuses retained text and restores focus on Escape", async () => {
  const { message, opened } = fixture();
  expect(fireEvent.keyDown(message, { key: "f", ctrlKey: true })).toBe(false);
  const search = await screen.findByRole("searchbox", { name: "Find chats" });
  await waitFor(() => expect(document.activeElement).toBe(search));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(opened).toHaveBeenCalledTimes(1);
  fireEvent.change(search, { target: { value: "retained query" } });
  const close = screen.getByRole("button", { name: "Close search" });
  close.focus();
  expect(fireEvent.keyDown(close, { key: "f", ctrlKey: true })).toBe(false);
  expect(document.activeElement).toBe(search);
  expect((search as HTMLInputElement).value).toBe("retained query");
  expect((search as HTMLInputElement).selectionEnd).toBe(14);
  await userEvent.setup().keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(message);
  expect(opened).toHaveBeenCalledTimes(1);
});

it("leaves native panes, other dialogs, modified keys, composition and handled events alone", () => {
  const { message } = fixture();
  for (const patch of [
    { shiftKey: true },
    { altKey: true },
    { isComposing: true },
    { repeat: true },
    { key: "g" },
    { ctrlKey: false },
  ]) {
    expect(
      fireEvent.keyDown(message, { key: "f", ctrlKey: true, ...patch }),
    ).toBe(true);
  }
  const file = screen.getByRole("button", { name: "File preview" });
  file.focus();
  expect(fireEvent.keyDown(file, { key: "f", ctrlKey: true })).toBe(true);
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.append(dialog);
  try {
    expect(fireEvent.keyDown(message, { key: "f", ctrlKey: true })).toBe(true);
  } finally {
    dialog.remove();
  }
  const handled = new KeyboardEvent("keydown", {
    key: "f",
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  handled.preventDefault();
  fireEvent(message, handled);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("opens search with either enabled key while preserving local find and terminal input", async () => {
  const { message } = fixture("both");
  const button = screen.getByRole("button", { name: "Search chats" });
  expect(button.getAttribute("aria-keyshortcuts")).toBe("Control+F Control+K");
  for (const name of ["File preview", "Terminal input", "Browser input"]) {
    const target = screen.getByRole("button", { name });
    target.focus();
    for (const key of ["f", "k"]) {
      expect(fireEvent.keyDown(target, { key, ctrlKey: true })).toBe(true);
    }
  }
  expect(screen.queryByRole("dialog")).toBeNull();
  message.focus();
  expect(fireEvent.keyDown(message, { key: "k", ctrlKey: true })).toBe(false);
  const search = await screen.findByRole("searchbox", { name: "Find chats" });
  button.focus();
  expect(fireEvent.keyDown(button, { key: "f", ctrlKey: true })).toBe(false);
  expect(document.activeElement).toBe(search);
});

it("honors Off without removing the Search control", async () => {
  const { message } = fixture("off");
  const search = screen.getByRole("button", { name: "Search chats" });
  expect(search.getAttribute("aria-keyshortcuts")).toBeNull();
  expect(fireEvent.keyDown(message, { key: "f", ctrlKey: true })).toBe(true);
  expect(fireEvent.keyDown(message, { key: "k", ctrlKey: true })).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(search);
  await screen.findByRole("searchbox", { name: "Find chats" });
});

it("removes the shortcut listener on unmount", () => {
  const { unmount } = fixture();
  unmount();
  expect(fireEvent.keyDown(window, { key: "f", ctrlKey: true })).toBe(true);
});

it("uses Command+F on Apple platforms", async () => {
  const platform = vi
    .spyOn(navigator, "platform", "get")
    .mockReturnValue("MacIntel");
  try {
    const { message } = fixture();
    expect(
      screen
        .getByRole("button", { name: "Search chats" })
        .getAttribute("aria-keyshortcuts"),
    ).toBe("Meta+F");
    expect(fireEvent.keyDown(message, { key: "f", ctrlKey: true })).toBe(true);
    expect(fireEvent.keyDown(message, { key: "f", metaKey: true })).toBe(false);
    const search = await screen.findByRole("searchbox", { name: "Find chats" });
    await waitFor(() => expect(document.activeElement).toBe(search));
  } finally {
    platform.mockRestore();
  }
});
