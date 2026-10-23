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
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { Desktop, type BrowserScale } from "./desktop";

const { connections } = vi.hoisted(() => ({
  connections: [] as {
    viewOnly: boolean;
    disconnect: ReturnType<typeof vi.fn>;
    canvas: HTMLCanvasElement;
  }[],
}));
vi.mock("@novnc/novnc", () => ({
  default: class extends EventTarget {
    viewOnly = false;
    disconnect = vi.fn();
    focus = vi.fn();
    clipboardPasteFrom = vi.fn();
    sendKey = vi.fn();
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
afterEach(() => {
  cleanup();
  connections.length = 0;
  vi.useRealTimers();
});

async function show() {
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
