// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  act,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useNavigationDrag, type DragSource } from "./navigation-drag";
import { placeItem, orderedProjects } from "./project-order";

const source: DragSource = {
  kind: "thread",
  id: "chat-a",
  label: "Chat A",
  thread: { role: "ordinary" } as never,
};
function point(
  element: Element | Window,
  type: string,
  x: number,
  y: number,
  pointerType = "mouse",
) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    buttons: type === "pointerup" ? 0 : 1,
    clientX: x,
    clientY: y,
  });
  Object.defineProperties(event, {
    pointerId: { value: 1 },
    pointerType: { value: pointerType },
  });
  fireEvent(element, event);
}
function fixture(item: DragSource = source) {
  const drop = vi.fn(),
    expand = vi.fn();
  function Fixture() {
    const drag = useNavigationDrag({ drop, expand });
    return (
      <div data-project-scroll>
        <section data-nav-group="one" data-project-key="one">
          <div data-nav-bucket="recent">
            <a
              href="#a"
              data-nav-thread="chat-a"
              onPointerDown={(event) => drag.begin(event, item)}
            >
              Chat A
            </a>
          </div>
          <div data-nav-bucket="recent">
            <a href="#b" data-nav-thread="chat-b">
              Chat B
            </a>
          </div>
        </section>
        <section data-nav-group="two" data-project-key="two">
          <div data-nav-heading>
            <button aria-expanded="false">Two</button>
          </div>
        </section>
        <section data-nav-group="@unavailable">
          <div data-nav-heading>Unavailable</div>
        </section>
        {drag.overlay}
      </div>
    );
  }
  render(<Fixture />);
  const a = screen.getByText("Chat A"),
    b = screen.getByText("Chat B");
  const scroller = document.querySelector<HTMLElement>(
    "[data-project-scroll]",
  )!;
  scroller.setPointerCapture = vi.fn();
  scroller.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      right: 220,
      bottom: 400,
      width: 220,
      height: 400,
    }) as DOMRect;
  b.getBoundingClientRect = () =>
    ({ left: 8, top: 60, bottom: 90, width: 204, height: 30 }) as DOMRect;
  const hit = vi.fn((): Element => b);
  Object.defineProperty(document, "elementFromPoint", {
    configurable: true,
    value: hit,
  });
  return { drop, expand, a, b, scroller, hit };
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("keeps a precise before/after target without changing rows, and suppresses only the drag's click", () => {
  const { a, b, drop, scroller } = fixture();
  point(a, "pointerdown", 20, 20);
  point(window, "pointermove", 24, 21);
  expect(scroller.setPointerCapture).not.toHaveBeenCalled();
  point(window, "pointermove", 20, 88);
  expect(scroller.setPointerCapture).toHaveBeenCalledWith(1);
  expect(
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(drop).not.toHaveBeenCalled();
  point(window, "pointerup", 20, 88);
  expect(drop).toHaveBeenCalledWith(source, {
    group: "one",
    id: "chat-b",
    edge: "after",
    peers: ["chat-a", "chat-b"],
  });
  expect(fireEvent.click(a, { detail: 1 })).toBe(false);
  expect(fireEvent.click(a, { detail: 1 })).toBe(true);
});
it.each(["outside", "escape", "cancel", "blur"])(
  "cancels %s without committing a stale target",
  (mode) => {
    const { a, drop } = fixture();
    point(a, "pointerdown", 20, 20);
    point(window, "pointermove", 20, 88);
    if (mode === "escape") fireEvent.keyDown(window, { key: "Escape" });
    if (mode === "cancel") point(window, "pointercancel", 20, 88);
    if (mode === "blur") fireEvent.blur(window);
    point(window, "pointerup", mode === "outside" ? 300 : 20, 88);
    expect(drop).not.toHaveBeenCalled();
  },
);
it("supports cross-project drops, expands after a deliberate hover, and scrolls with a stationary pointer", () => {
  vi.useFakeTimers();
  const { a, hit, drop, expand, scroller } = fixture();
  const target = screen.getByText("Two");
  hit.mockReturnValue(target);
  point(a, "pointerdown", 20, 20);
  point(window, "pointermove", 20, 390);
  act(() => vi.advanceTimersByTime(700));
  expect(expand).toHaveBeenCalledExactlyOnceWith("two");
  expect(scroller.scrollTop).toBeGreaterThan(0);
  point(window, "pointerup", 20, 390);
  expect(drop).toHaveBeenCalledWith(source, {
    group: "two",
    id: "two",
    edge: "inside",
    peers: [],
  });
  const position = scroller.scrollTop;
  act(() => vi.advanceTimersByTime(200));
  expect(scroller.scrollTop).toBe(position);
});
it("does not transfer Coordinators; touch keeps scrolling", () => {
  const { a, hit, drop, scroller } = fixture({
    ...source,
    thread: { role: "coordinator" },
  } as DragSource);
  point(a, "pointerdown", 20, 20, "touch");
  point(window, "pointermove", 20, 90, "touch");
  expect(scroller.setPointerCapture).not.toHaveBeenCalled();
  hit.mockReturnValue(screen.getByText("Two"));
  point(a, "pointerdown", 20, 20);
  point(window, "pointermove", 20, 90);
  point(window, "pointerup", 20, 90);
  expect(drop).not.toHaveBeenCalled();
});
it("inserts relative to a known peer and retains newly discovered rows", () => {
  expect(placeItem(["a", "b", "c"], "a", "c", false)).toEqual(["b", "a", "c"]);
  expect(placeItem(["a", "b", "c"], "a", "c", true)).toEqual(["b", "c", "a"]);
  expect(placeItem(["a", "b"], "a", "gone", true)).toEqual(["a", "b"]);
  expect(orderedProjects(["a", "b", "new"], ["gone", "b", "a"])).toEqual([
    "b",
    "a",
    "new",
  ]);
});

it("rejects unavailable Projects and omits collapsed rows from ordering peers", () => {
  const { a, b, hit, drop } = fixture();
  hit.mockReturnValue(screen.getByText("Unavailable"));
  point(a, "pointerdown", 20, 20);
  point(window, "pointermove", 20, 90);
  point(window, "pointerup", 20, 90);
  expect(drop).not.toHaveBeenCalled();
  const hidden = document.createElement("div");
  hidden.hidden = true;
  hidden.innerHTML =
    '<div data-nav-bucket="recent"><a data-nav-thread="hidden">Hidden</a></div>';
  b.closest("section")!.append(hidden);
  hit.mockReturnValue(b);
  point(a, "pointerdown", 20, 20);
  point(window, "pointermove", 20, 70);
  point(window, "pointerup", 20, 70);
  expect(drop).toHaveBeenCalledWith(source, {
    group: "one",
    id: "chat-b",
    edge: "before",
    peers: ["chat-a", "chat-b"],
  });
});
