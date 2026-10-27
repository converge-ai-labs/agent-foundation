// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { Desktop, type BrowserScale } from "./desktop";
import { ReturnToChat } from "../native/capture";

vi.mock("./annotation-editor", () => ({
  default: ({
    onClose,
    onCopied,
  }: {
    onClose: () => void;
    onCopied: () => void;
  }) => (
    <div role="dialog" aria-label="Screenshot editor">
      <button onClick={onClose}>Cancel screenshot</button>
      <button onClick={onCopied}>Copy screenshot</button>
    </div>
  ),
}));

const { connections } = vi.hoisted(() => ({
  connections: [] as (EventTarget & {
    viewOnly: boolean;
    disconnect: ReturnType<typeof vi.fn>;
    sendKey: ReturnType<typeof vi.fn>;
    clipboardPasteFrom: ReturnType<typeof vi.fn>;
    canvas: HTMLCanvasElement;
  })[],
}));
vi.mock("@novnc/novnc", () => ({
  default: class extends EventTarget {
    viewOnly = false;
    disconnect = vi.fn();
    focus = vi.fn();
    clipboardPasteFrom = vi.fn();
    sendKey = vi.fn();
    getImageData = () => ({
      width: 800,
      height: 600,
      data: new Uint8ClampedArray([0, 0, 0, 255]),
    });
    canvas: HTMLCanvasElement;
    constructor(root: HTMLElement) {
      super();
      this.canvas = document.createElement("canvas");
      this.canvas.width = 800;
      this.canvas.height = 600;
      root.appendChild(this.canvas);
      connections.push(this);
      setTimeout(() => this.dispatchEvent(new Event("connect")), 0);
    }
  },
}));
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    putImageData: vi.fn(),
    getImageData: () => ({ data: [0, 0, 0, 255] }),
  } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  cleanup();
  connections.length = 0;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function show(returnToChat = vi.fn()) {
  const post = vi.fn(
    async (_url: string, options: { body: { control_token: string } }) => ({
      data: {
        ticket: "fixture-ticket",
        password: "fixture-password",
        control: !!options.body.control_token,
      },
    }),
  );
  // The transport identity must remain stable across ordinary local updates.
  const client = { POST: post };
  const renderView = (token = "", scale: BrowserScale = "fit") => (
    <TransportContext value={{ client } as unknown as Transport}>
      <ReturnToChat value={returnToChat}>
        <Desktop
          threadId="thread-a"
          session={{
            session_id: "binding-a",
            status: "ready",
            control: token ? "human" : "agent",
          }}
          controlToken={token}
          busy={false}
          scale={scale}
        />
      </ReturnToChat>
    </TransportContext>
  );
  const rendered = render(renderView());
  await waitFor(() => expect(screen.queryByText("Connecting…")).toBeNull());
  return {
    post,
    update: (token = "", scale: BrowserScale = "fit") =>
      rendered.rerender(renderView(token, scale)),
  };
}

it("uses noVNC for read-only viewing and shows the temporary control hint only on click", async () => {
  await show();
  expect(connections[0].viewOnly).toBe(true);
  const guard = screen.getByRole("button", { name: "Read-only browser" });
  vi.useFakeTimers();
  fireEvent.pointerEnter(guard);
  fireEvent.focus(guard);
  expect(screen.queryByText("Take control to interact")).toBeNull();
  fireEvent.click(guard);
  expect(screen.getByText("Take control to interact")).toBeTruthy();
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.queryByText("Take control to interact")).toBeNull();
});

it("resizes the local viewing surface without reconnecting or changing native input authority", async () => {
  const view = await show();
  view.update("", "200");
  await waitFor(() =>
    expect(
      document.querySelector<HTMLElement>("[data-browser-screen]")?.style.width,
    ).toBe("1600px"),
  );
  expect(connections).toHaveLength(1);
  expect(connections[0].disconnect).not.toHaveBeenCalled();
  view.update("", "fit");
  expect(
    document.querySelector<HTMLElement>("[data-browser-screen]")?.style.width,
  ).toBe("");
});

it.each([
  ["disconnect", "Disconnected"],
  ["securityfailure", "Connection rejected"],
])("reconnects with fresh viewer access after %s", async (event, message) => {
  const view = await show();
  act(() => connections[0].dispatchEvent(new Event(event)));
  expect(screen.getByRole("status").textContent).toBe(message);
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(connections).toHaveLength(2));
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  expect(view.post).toHaveBeenCalledTimes(2);
  expect(view.post).toHaveBeenLastCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "binding-a", control_token: "" },
    }),
  );
});

it("requests fresh native control access on takeover and revokes the old connection on handback", async () => {
  const view = await show();
  view.update("controller-a");
  await waitFor(() => expect(connections).toHaveLength(2));
  await waitFor(() => expect(connections[1].viewOnly).toBe(false));
  expect(connections[0].disconnect).toHaveBeenCalledOnce();
  expect(view.post).toHaveBeenLastCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "binding-a", control_token: "controller-a" },
    }),
  );
  view.update();
  await waitFor(() => expect(connections).toHaveLength(3));
  expect(connections[2].viewOnly).toBe(true);
  expect(connections[1].disconnect).toHaveBeenCalledOnce();
});

it("focuses the local IME input on takeover and removes it on handback", async () => {
  const view = await show();
  expect(
    screen.queryByRole("textbox", { name: "Remote browser keyboard input" }),
  ).toBeNull();
  view.update("controller-a");
  const input = await screen.findByRole("textbox", {
    name: "Remote browser keyboard input",
  });
  await waitFor(() => expect(document.activeElement).toBe(input));
  fireEvent.compositionStart(input);
  fireEvent.compositionEnd(input, { data: "你好" });
  await waitFor(() =>
    expect(connections[1].clipboardPasteFrom).toHaveBeenCalledWith("你好"),
  );
  expect(connections[1].sendKey).toHaveBeenCalledWith(118, "KeyV");
  const count = connections[1].clipboardPasteFrom.mock.calls.length;
  view.update();
  await waitFor(() => expect(connections).toHaveLength(3));
  expect(
    screen.queryByRole("textbox", { name: "Remote browser keyboard input" }),
  ).toBeNull();
  fireEvent.compositionEnd(input, { data: "不要发送" });
  expect(connections[1].clipboardPasteFrom).toHaveBeenCalledTimes(count);
});

it("captures a read-only framebuffer without acquiring control or reconnecting", async () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    putImageData: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  const view = await show();
  const capture = screen.getByRole("button", {
    name: "Capture and annotate screenshot",
  });
  fireEvent.click(capture);
  await screen.findByRole("dialog", { name: "Screenshot editor" });
  expect(view.post).toHaveBeenCalledOnce();
  expect(connections).toHaveLength(1);
  expect(connections[0].viewOnly).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Cancel screenshot" }));
  await waitFor(() => expect(document.activeElement).toBe(capture));
  expect(connections[0].viewOnly).toBe(true);
});
it("suspends controller input while editing and returns to chat after copy", async () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    putImageData: vi.fn(),
    getImageData: () => ({ data: [0, 0, 0, 255] }),
  } as unknown as CanvasRenderingContext2D);
  const returnToChat = vi.fn();
  const view = await show(returnToChat);
  view.update("controller-a");
  const input = await screen.findByRole("textbox", {
    name: "Remote browser keyboard input",
  });
  await waitFor(() => expect(document.activeElement).toBe(input));
  fireEvent.click(
    screen.getByRole("button", { name: "Capture and annotate screenshot" }),
  );
  await screen.findByRole("dialog", { name: "Screenshot editor" });
  expect(connections[1].viewOnly).toBe(true);
  connections[1].sendKey.mockClear();
  fireEvent.compositionEnd(input, { data: "local annotation" });
  fireEvent.keyDown(input, { key: "a", code: "KeyA" });
  await Promise.resolve();
  expect(connections[1].sendKey).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Copy screenshot" }));
  await waitFor(() => expect(returnToChat).toHaveBeenCalledOnce());
  expect(connections[1].viewOnly).toBe(false);
  expect(connections).toHaveLength(2);
  expect(connections[1].disconnect).not.toHaveBeenCalled();
});
