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
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { MobileNavigation } from "../shell/sidebar";

vi.mock("../transport/context", () => ({
  useProjects: () => ({ data: [] }),
}));
const searchRows = vi.hoisted(
  () => [] as { thread: { thread_id: string; title: string } }[],
);
vi.mock("./queries", () => ({
  useThreads: () => ({
    data: { pages: [{ rows: searchRows }] },
    isSuccess: true,
  }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  searchRows.length = 0;
});

function fixture() {
  const opened = vi.fn();
  const view = render(
    <ChatSearchProvider>
      <textarea aria-label="Message" />
      <ChatSearch onOpen={opened} />
      <div hidden>
        <ChatSearch onOpen={() => {}} />
      </div>
      <div data-native-find="">
        <button>File preview</button>
      </div>
    </ChatSearchProvider>,
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

it("opens the leading result with Enter from the query", async () => {
  searchRows.push(
    { thread: { thread_id: "thread_first", title: "First match" } },
    { thread: { thread_id: "thread_second", title: "Second match" } },
  );
  function Location() {
    return <output aria-label="Location">{useLocation().pathname}</output>;
  }
  render(
    <MemoryRouter>
      <ChatSearchProvider>
        <ChatSearch onOpen={() => {}} />
        <Location />
      </ChatSearchProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Search chats" }));
  const input = await screen.findByRole("searchbox", { name: "Find chats" });
  await userEvent.type(input, "match");
  await screen.findByRole("link", { name: "First match" });
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("status", { name: "Location" }).textContent).toBe(
    "/threads/thread_first",
  );
});

it("replaces mobile navigation with search without restoring drawer focus over the input", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  function Navigation() {
    const [open, setOpen] = useState(true);
    return (
      <MobileNavigation
        open={open}
        onOpenChange={setOpen}
        finalFocus={() => null}
      >
        <ChatSearch onOpen={() => {}} />
      </MobileNavigation>
    );
  }
  render(
    <ChatSearchProvider>
      <Navigation />
    </ChatSearchProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Search chats" }),
  );
  const input = await screen.findByRole("searchbox", { name: "Find chats" });
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Close navigation" }),
    ).toBeNull(),
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
