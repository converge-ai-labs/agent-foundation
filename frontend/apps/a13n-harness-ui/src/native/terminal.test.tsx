// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import TerminalScreen from "./terminal-screen";
import { TerminalPanel } from "./terminal";
import { ComposerDrafts } from "../conversations/composer";
import { ThreadDraft, encode } from "../conversations/draft";
import * as Y from "yjs";

const emulator = vi.hoisted(() => ({
  input: (_: string) => {},
  disposed: vi.fn(),
  resized: vi.fn(),
  selection: "",
  select: () => {},
  findNext: vi.fn(() => true),
  findPrevious: vi.fn(() => true),
}));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options = { disableStdin: true, theme: {} };
    loadAddon() {}
    open() {}
    focus() {}
    reset() {}
    buffer = { active: { getLine: () => undefined } };
    registerLinkProvider() {
      return { dispose() {} };
    }
    getSelection() {
      return emulator.selection;
    }
    onSelectionChange(fn: () => void) {
      emulator.select = fn;
      return { dispose() {} };
    }
    attachCustomKeyEventHandler() {}
    resize = emulator.resized;
    onData(fn: (text: string) => void) {
      emulator.input = fn;
      return { dispose() {} };
    }
    onBinary() {
      return { dispose() {} };
    }
    write(_text: string, done: () => void) {
      done();
    }
    dispose = emulator.disposed;
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    proposeDimensions() {
      return { rows: 20, cols: 90 };
    }
  },
}));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {
    findNext = emulator.findNext;
    findPrevious = emulator.findPrevious;
    clearDecorations() {}
    onDidChangeResults() {
      return { dispose() {} };
    }
  },
}));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));
class Socket {
  static OPEN = 1;
  static all: Socket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  onopen = () => {};
  onmessage = (_: { data: string }) => {};
  onclose = (_: { code: number }) => {};
  send = vi.fn();
  close = vi.fn();
  constructor() {
    Socket.all.push(this);
  }
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  emulator.selection = "";
  localStorage.clear();
});
function setup(sessions: Schema<"TerminalView">[] = [view]) {
  Socket.all = [];
  // jsdom has no Web Animations implementation used by the shared modal viewport.
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const get = vi.fn(async (url: string) => ({
    data: url === "/api/projects" ? [] : sessions,
  }));
  const post = vi.fn();
  const remove = vi.fn();
  const transport = {
    client: { GET: get, POST: post, DELETE: remove },
    key: "test-only",
  } as unknown as Transport;
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queries}>
        <TransportContext value={transport}>{children}</TransportContext>
      </QueryClientProvider>
    );
  }
  return { Wrapper, post, remove };
}
const view: Schema<"TerminalView"> = {
  terminal_id: "terminal-one",
  cwd: "/native/code",
  project_id: "project-one",
  shell: "/bin/sh",
  state: "running",
  exit_code: null,
  controller: null,
  control_epoch: 0,
  participants: ["participant-me"],
  output_start: 0,
  output_end: 0,
  rows: 24,
  columns: 80,
};
function emit(socket: Socket, controller: string | null, epoch: number) {
  const frame: Schema<"TerminalFrame"> = {
    kind: "terminal",
    participant_id: "participant-me",
    terminal: { ...view, controller, control_epoch: epoch },
    start: 0,
    end: 0,
    gap: false,
    data_base64: "",
  };
  fireEvent(window, new Event("focus"));
  socket.onmessage({ data: JSON.stringify(frame) });
}

it("controller controls stay disabled until acknowledgement; collapse detaches without disposing the emulator or recreating a session", async () => {
  const f = setup();
  const unauthorized = vi.fn();
  const component = render(
    <TerminalScreen id="terminal-one" visible unauthorized={unauthorized} />,
    { wrapper: f.Wrapper },
  );
  const socket = Socket.all[0];
  emit(socket, null, 0);
  fireEvent.click(await screen.findByRole("button", { name: "Take control" }));
  expect(
    screen
      .getByRole("button", { name: "Confirming…" })
      .hasAttribute("disabled"),
  ).toBe(true);
  emulator.input("no pending input");
  expect(socket.send).toHaveBeenCalledTimes(1);
  emit(socket, "participant-me", 1);
  await screen.findByText(/You have control/);
  emulator.input("echo once\r");
  expect(JSON.parse(socket.send.mock.calls.at(-1)![0])).toMatchObject({
    kind: "input",
    text: "echo once\r",
    control_epoch: 1,
  });
  emit(socket, "participant-other", 2);
  await screen.findByRole("button", { name: "Take over input" });
  const count = socket.send.mock.calls.length;
  emulator.input("stale");
  expect(socket.send).toHaveBeenCalledTimes(count);
  component.rerender(
    <TerminalScreen
      id="terminal-one"
      visible={false}
      unauthorized={unauthorized}
    />,
  );
  await waitFor(() => expect(socket.close).toHaveBeenCalledTimes(1));
  expect(emulator.disposed).not.toHaveBeenCalled();
  component.rerender(
    <TerminalScreen id="terminal-one" visible unauthorized={unauthorized} />,
  );
  expect(Socket.all).toHaveLength(2);
  emit(Socket.all[1], null, 3);
  await screen.findByRole("button", { name: "Take control" });
  expect(Socket.all[1].send).not.toHaveBeenCalled();
  expect(f.post).not.toHaveBeenCalled();
});

it("creation uses the reviewed native cwd, uncertain create cannot be repeated, and process closure requires explicit confirmation", async () => {
  const f = setup();
  const selected = vi.fn();
  const collapse = vi.fn();
  f.post.mockRejectedValue(new Error("Response lost"));
  const props = {
    visible: true,
    directory: "/native/start",
    projectId: "project-one",
    selected: "",
    select: selected,
    collapse,
    unauthorized: vi.fn(),
  };
  const component = render(<TerminalPanel {...props} />, {
    wrapper: f.Wrapper,
  });
  expect(screen.queryByRole("button", { name: "Choose folder…" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "New terminal" }));
  await screen.findByText("Response lost");
  expect(
    screen
      .getByRole("button", { name: "New terminal" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(f.post.mock.calls[0]).toEqual([
    "/api/host/terminals",
    { body: { cwd: "/native/start", project_id: "project-one" } },
  ]);
  expect(selected).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Refresh terminal sessions" }),
  );
  component.rerender(<TerminalPanel {...props} selected="terminal-one" />);
  fireEvent.click(await screen.findByRole("button", { name: "End session" }));
  expect(f.remove).not.toHaveBeenCalled();
  f.remove.mockResolvedValue({ data: { ...view, state: "closed" } });
  fireEvent.click(
    screen.getByRole("button", { name: "End session for everyone" }),
  );
  await waitFor(() => expect(f.remove).toHaveBeenCalledTimes(1));
  expect(f.remove.mock.calls[0]).toEqual([
    "/api/host/terminals/{terminal_id}",
    { params: { path: { terminal_id: "terminal-one" } } },
  ]);
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "New terminal" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  f.post.mockResolvedValue({ data: view });
  fireEvent.click(screen.getByRole("button", { name: "New terminal" }));
  await waitFor(() => expect(f.post).toHaveBeenCalledTimes(2));
});

it("the creator claims input once after attachment, waits for acknowledgement and never reclaims on reconnect", async () => {
  const f = setup();
  f.post.mockResolvedValue({ data: view });
  const unauthorized = vi.fn();
  function Panel() {
    const [selected, select] = useState("");
    return (
      <TerminalPanel
        visible
        directory="/native/start"
        projectId="project-one"
        selected={selected}
        select={select}
        collapse={() => {}}
        unauthorized={unauthorized}
      />
    );
  }
  render(<Panel />, { wrapper: f.Wrapper });
  fireEvent.click(screen.getByRole("button", { name: "New terminal" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() => expect(Socket.all).toHaveLength(1));
  const socket = Socket.all[0];
  expect(socket.send).not.toHaveBeenCalled();
  emit(socket, null, 0);
  await screen.findByRole("button", { name: "Confirming…" });
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
    kind: "control",
    control_epoch: 0,
    release: false,
  });
  emulator.input("not yet");
  expect(socket.send).toHaveBeenCalledTimes(1);
  emit(socket, "participant-me", 1);
  await screen.findByText("You have control");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  const reconnected = Socket.all[1];
  emit(reconnected, null, 2);
  await screen.findByText("Viewing only");
  expect(reconnected.send).not.toHaveBeenCalled();
  expect(f.post).toHaveBeenCalledTimes(1);
});

it("a creator attachment does not take over from someone who already claimed the new session", async () => {
  const f = setup();
  const claimCreated = vi.fn(() => true);
  render(
    <TerminalScreen
      id="terminal-one"
      visible
      claimCreated={claimCreated}
      unauthorized={vi.fn()}
    />,
    { wrapper: f.Wrapper },
  );
  emit(Socket.all[0], "participant-other", 1);
  await screen.findByRole("button", { name: "Take over input" });
  expect(claimCreated).toHaveBeenCalledTimes(1);
  expect(Socket.all[0].send).not.toHaveBeenCalled();
});

it("only shows the current Project's terminals and never ends another Project's sessions on navigation", async () => {
  const other = {
    ...view,
    terminal_id: "terminal-other",
    project_id: "project-two",
    cwd: "/other/work",
  };
  const f = setup([
    view,
    other,
    {
      ...view,
      terminal_id: "terminal-unassigned",
      project_id: null,
      cwd: "/unassigned",
    },
  ]);
  const props = {
    visible: true,
    directory: "/native",
    projectId: "project-one",
    selected: "",
    select: vi.fn(),
    collapse: vi.fn(),
    unauthorized: vi.fn(),
  };
  const component = render(<TerminalPanel {...props} />, {
    wrapper: f.Wrapper,
  });
  await screen.findByRole("button", { name: "code" });
  expect(screen.queryByRole("button", { name: "work" })).toBeNull();
  expect(screen.queryByRole("button", { name: "unassigned" })).toBeNull();
  component.rerender(
    <TerminalPanel
      {...props}
      projectId="project-two"
      directory="/other"
      selected="terminal-one"
    />,
  );
  await screen.findByRole("button", { name: "work" });
  expect(screen.queryByRole("button", { name: "code" })).toBeNull();
  expect(screen.queryByRole("button", { name: "End session" })).toBeNull();
  expect(f.remove).not.toHaveBeenCalled();
  expect(f.post).not.toHaveBeenCalled();
  component.rerender(<TerminalPanel {...props} projectId="" directory="" />);
  expect(
    screen
      .getByRole("button", { name: "New terminal" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("a terminal creation completing after a Project switch cannot select a terminal in the new Project", async () => {
  const f = setup();
  let finish!: (value: { data: Schema<"TerminalView"> }) => void;
  f.post.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const props = {
    visible: true,
    directory: "/native",
    projectId: "project-one",
    selected: "",
    select: vi.fn(),
    collapse: vi.fn(),
    unauthorized: vi.fn(),
  };
  const component = render(<TerminalPanel {...props} />, {
    wrapper: f.Wrapper,
  });
  fireEvent.click(screen.getByRole("button", { name: "New terminal" }));
  component.rerender(
    <TerminalPanel {...props} projectId="project-two" directory="/other" />,
  );
  finish({ data: view });
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "New terminal" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(props.select).not.toHaveBeenCalled();
  expect(f.remove).not.toHaveBeenCalled();
});

it.each(["project-two", ""])(
  "a terminal link outside Project %s cannot attach or report active focus",
  async (projectId) => {
    const f = setup();
    const onActive = vi.fn();
    render(
      <TerminalPanel
        visible
        directory="/other"
        projectId={projectId}
        selected="terminal-one"
        select={vi.fn()}
        onActive={onActive}
        collapse={vi.fn()}
        unauthorized={vi.fn()}
      />,
      { wrapper: f.Wrapper },
    );
    await screen.findByText(
      "This terminal is not available in the current project. Open a conversation in its project to view it.",
    );
    expect(onActive).toHaveBeenLastCalledWith("");
    expect(onActive.mock.calls.some(([id]) => id)).toBe(false);
    expect(Socket.all).toHaveLength(0);
    expect(f.post).not.toHaveBeenCalled();
  },
);

it("keeps explicit disconnect across hide/show without recreating a screen", async () => {
  const f = setup();
  const unauthorized = vi.fn();
  const component = render(
    <TerminalScreen id="terminal-one" visible unauthorized={unauthorized} />,
    { wrapper: f.Wrapper },
  );
  emit(Socket.all[0], null, 0);
  fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
  component.rerender(
    <TerminalScreen
      id="terminal-one"
      visible={false}
      unauthorized={unauthorized}
    />,
  );
  component.rerender(
    <TerminalScreen id="terminal-one" visible unauthorized={unauthorized} />,
  );
  expect(Socket.all).toHaveLength(1);
  expect(emulator.disposed).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Reconnect" })).toBeTruthy();
});

it("searches retained output and appends selection only to the currently selected Thread", async () => {
  const f = setup();
  const one = new ThreadDraft();
  const two = new ThreadDraft();
  for (const [id, draft] of [
    ["one", one],
    ["two", two],
  ] as const) {
    draft.doc.getText("text").insert(0, `Draft ${id}`);
    draft.receive({
      draft_id: `draft-${id}`,
      participant_id: "me",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
  }
  const unauthorized = vi.fn();
  const drafts = new Map([
    ["one", one],
    ["two", two],
  ]);
  const content = (threadId: string) => (
    <ComposerDrafts value={drafts}>
      <TerminalScreen
        id="terminal-one"
        visible
        threadId={threadId}
        unauthorized={unauthorized}
      />
    </ComposerDrafts>
  );
  const component = render(content("one"), { wrapper: f.Wrapper });
  act(() => {
    emulator.selection = "hello ``` output";
    emulator.select();
  });
  component.rerender(content("two"));
  fireEvent.click(
    screen.getByRole("button", { name: "Add selection to prompt" }),
  );
  expect(one.doc.getText("text").toString()).toBe("Draft one");
  expect(two.doc.getText("text").toString()).toContain(
    "Draft two\n\nSelected output from Server terminal terminal-one",
  );
  expect(two.doc.getText("text").toString()).toContain(
    "````text\nhello ``` output\n````",
  );
  expect(f.post).not.toHaveBeenCalled();
  expect(Socket.all).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Find in output" }));
  fireEvent.change(screen.getByLabelText("Find in retained terminal output"), {
    target: { value: "hello" },
  });
  expect(emulator.findNext).toHaveBeenLastCalledWith(
    "hello",
    expect.objectContaining({ incremental: true }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Previous match" }));
  expect(emulator.findPrevious).toHaveBeenLastCalledWith(
    "hello",
    expect.objectContaining({ decorations: expect.any(Object) }),
  );
});
