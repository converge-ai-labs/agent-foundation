// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  pointerCoordinates,
  pointerPosition,
  SharedPointers,
} from "./shared-pointers";
import type { Schema } from "../transport/client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
const target = { kind: "conversation", thread_id: "thread-one" } as const;
function setup() {
  const ws = new EventTarget() as EventTarget & {
    readyState: number;
    bufferedAmount: number;
    send: ReturnType<typeof vi.fn>;
  };
  ws.readyState = WebSocket.OPEN;
  ws.bufferedAmount = 0;
  ws.send = vi.fn();
  const presence: Schema<"PresenceFrame"> = {
    participant_id: "own",
    participants: [
      {
        participant_id: "peer",
        display_name: "Bob",
        color: "#2563eb",
        foreground: true,
        availability: "available",
        focus: { target },
      },
    ],
  };
  const socket = { current: ws as unknown as WebSocket };
  const rendered = render(
    <>
      <div data-presence-anchor="composer">
        <span>Prompt surface</span>
      </div>
      <SharedPointers socket={socket} presence={presence} focus={{ target }} />
    </>,
  );
  const anchor = screen.getByText("Prompt surface").parentElement!;
  vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({
    left: 100,
    top: 100,
    right: 500,
    bottom: 300,
    width: 400,
    height: 200,
  } as DOMRect);
  Object.defineProperty(document, "elementFromPoint", {
    configurable: true,
    value: vi.fn(() => anchor),
  });
  const receive = (frame: Schema<"PointerFrame">) =>
    act(() => {
      ws.dispatchEvent(
        new MessageEvent("message", { data: JSON.stringify(frame) }),
      );
    });
  return { ws, socket, presence, anchor, receive, ...rendered };
}

it("maps normalized positions to the matching visible surface, not the sender's viewport", () => {
  const { anchor } = setup();
  expect(pointerPosition(anchor, 300, 150)).toEqual({
    anchor: "composer",
    x: 0.5,
    y: 0.25,
  });
  expect(pointerCoordinates({ anchor: "composer", x: 0.5, y: 0.25 })).toEqual({
    x: 300,
    y: 150,
  });
  expect(pointerPosition(document.body, 300, 150)).toBeNull();
  expect(pointerPosition(anchor, 600, 150)).toBeNull();
  expect(pointerCoordinates({ anchor: "missing", x: 0, y: 0 })).toBeNull();
  vi.spyOn(document, "elementFromPoint").mockReturnValue(document.body);
  expect(
    pointerCoordinates({ anchor: "composer", x: 0.5, y: 0.25 }),
  ).toBeNull();
});

it("renders same-page peers and clears pointers on navigation, away status and stale delivery", () => {
  vi.useFakeTimers();
  const { receive, presence, socket, rerender } = setup();
  receive({
    kind: "pointers",
    target,
    pointers: { peer: { anchor: "composer", x: 0.5, y: 0.25 } },
  });
  expect(screen.getByText("Bob")).toBeTruthy();
  act(() => vi.advanceTimersByTime(6000));
  expect(screen.queryByText("Bob")).toBeNull();
  receive({
    kind: "pointers",
    target: { ...target, thread_id: "thread-other" },
    pointers: { peer: { anchor: "composer", x: 0.5, y: 0.25 } },
  });
  expect(screen.queryByText("Bob")).toBeNull();
  receive({
    kind: "pointers",
    target,
    pointers: { peer: { anchor: "composer", x: 0.5, y: 0.25 } },
  });
  rerender(
    <>
      <div data-presence-anchor="composer">Prompt surface</div>
      <SharedPointers
        socket={socket}
        presence={{
          ...presence,
          participants: presence.participants.map((peer) => ({
            ...peer,
            foreground: false,
          })),
        }}
        focus={{ target }}
      />
    </>,
  );
  expect(screen.queryByText("Bob")).toBeNull();
});

it("coalesces movement, clears on blur and does not publish unsupported or touch positions", () => {
  vi.useFakeTimers();
  const { ws, anchor } = setup();
  const move = (x: number, pointerType = "mouse") => {
    const event = new MouseEvent("pointermove", {
      bubbles: true,
      clientX: x,
      clientY: 150,
    });
    Object.defineProperty(event, "pointerType", { value: pointerType });
    fireEvent(anchor, event);
  };
  move(200);
  move(300);
  move(400);
  expect(ws.send).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(100));
  expect(ws.send).toHaveBeenCalledTimes(1);
  expect(JSON.parse(ws.send.mock.calls[0][0]).pointer).toEqual({
    anchor: "composer",
    x: 0.75,
    y: 0.25,
  });
  fireEvent.blur(window);
  expect(JSON.parse(ws.send.mock.calls.at(-1)![0]).pointer).toBeNull();
  ws.send.mockClear();
  move(300, "touch");
  act(() => vi.advanceTimersByTime(100));
  expect(ws.send).not.toHaveBeenCalled();
  move(300);
  fireEvent.blur(window);
  act(() => vi.advanceTimersByTime(100));
  expect(ws.send).not.toHaveBeenCalled();
});
