import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useRef } from "react";
import { useTranscriptScroll } from "./use-transcript-scroll";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup() {
  let resize!: () => void;
  let scroll!: ReturnType<typeof useTranscriptScroll>;
  let height = 2000;
  let inserted = 0;
  const writes = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
  function Transcript() {
    const ref = useRef<HTMLDivElement>(null);
    scroll = useTranscriptScroll(ref);
    return (
      <div
        data-session-stage
        ref={(node) => {
          if (!node) return;
          Object.defineProperties(node, {
            scrollHeight: { configurable: true, get: () => height },
            clientHeight: { configurable: true, value: 500 },
          });
          node.scrollTo = (options) => {
            if (typeof options !== "object") return;
            writes(options);
            if (options.behavior !== "smooth")
              node.scrollTop = Math.min(options.top ?? 0, height - 500);
          };
        }}
      >
        <div ref={ref}>
          <div
            data-run="current"
            ref={(node) => {
              if (node)
                node.getBoundingClientRect = () =>
                  new DOMRect(
                    0,
                    inserted -
                      node.closest<HTMLElement>("[data-session-stage]")!
                        .scrollTop,
                    700,
                    2000,
                  );
            }}
          >
            Messages
          </div>
        </div>
        <output>{scroll.showJump ? "Jump to latest" : "At latest"}</output>
      </div>
    );
  }
  const view = render(<Transcript />);
  const stage = view.container.querySelector<HTMLElement>(
    "[data-session-stage]",
  )!;
  return {
    stage,
    writes,
    scroll: () => scroll,
    resize: () => act(resize),
    grow(amount: number) {
      height += amount;
    },
    prepend(amount: number) {
      height += amount;
      inserted += amount;
    },
    readAt(top: number) {
      fireEvent.wheel(stage, { deltaY: -200 });
      stage.scrollTop = top;
      fireEvent.scroll(stage);
    },
  };
}

it("stops following on the reader's gesture before a resize can steal the viewport", () => {
  const view = setup();
  expect(view.stage.scrollTop).toBe(1500);
  fireEvent.wheel(view.stage, { deltaY: -200 });
  view.grow(300);
  view.resize();
  expect(view.stage.scrollTop).toBe(1500);
  expect(screen.getByText("Jump to latest")).toBeTruthy();
});

it("restores a prepended run instantly without re-enabling live following", () => {
  const view = setup();
  view.readAt(100);
  const before = view.stage
    .querySelector("[data-run]")!
    .getBoundingClientRect().top;
  const restore = view.scroll().preservePosition();
  view.prepend(800);
  act(restore);
  fireEvent.scroll(view.stage);
  view.resize();
  expect(
    view.stage.querySelector("[data-run]")!.getBoundingClientRect().top,
  ).toBe(before);
  expect(view.stage.scrollTop).toBe(900);
  expect(screen.getByText("Jump to latest")).toBeTruthy();
  expect(view.writes.mock.calls.at(-1)?.[0].behavior).toBe("instant");
});

it("keeps layout updates from restarting an explicit smooth jump", () => {
  const view = setup();
  view.readAt(100);
  act(() => view.scroll().jumpToLatest());
  view.writes.mockClear();
  view.grow(300);
  view.resize();
  view.stage.scrollTop = 700;
  fireEvent.scroll(view.stage);
  expect(view.writes).not.toHaveBeenCalled();
  expect(screen.getByText("At latest")).toBeTruthy();
  fireEvent(view.stage, new Event("scrollend"));
  expect(view.stage.scrollTop).toBe(1800);
  view.grow(200);
  view.resize();
  expect(view.stage.scrollTop).toBe(2000);
});

it("lets a reader interrupt a jump and follows again when they reach the end", () => {
  const view = setup();
  view.readAt(100);
  act(() => view.scroll().jumpToLatest());
  view.readAt(300);
  view.resize();
  expect(view.stage.scrollTop).toBe(300);
  view.stage.scrollTop = 1500;
  fireEvent.scroll(view.stage);
  expect(screen.getByText("At latest")).toBeTruthy();
  view.grow(200);
  view.resize();
  expect(view.stage.scrollTop).toBe(1700);
});

it("returns to the latest output once at submission, then respects further reader scrolling", () => {
  const view = setup();
  view.readAt(100);
  act(() => view.scroll().followLatest());
  view.grow(100);
  view.resize();
  expect(view.stage.scrollTop).toBe(1600);
  view.readAt(900);
  view.grow(400);
  view.resize();
  expect(view.stage.scrollTop).toBe(900);
});

it.each(["wheel", "ArrowDown", "PageDown", "End", " "])(
  "keeps the jump hidden and follows new output after %s at the bottom without a scroll event",
  (input) => {
    const view = setup();
    // Browser scroll heights are rounded, while scrollTop can be fractional.
    view.stage.scrollTop = 1499.5;
    if (input === "wheel") fireEvent.wheel(view.stage, { deltaY: 200 });
    else fireEvent.keyDown(view.stage, { key: input });
    expect(screen.getByText("At latest")).toBeTruthy();
    view.grow(300);
    view.resize();
    expect(view.stage.scrollTop).toBe(1800);
    expect(screen.getByText("At latest")).toBeTruthy();
  },
);

it("shows the jump only once an upward gesture actually leaves the latest output", () => {
  const view = setup();
  fireEvent.wheel(view.stage, { deltaY: -200 });
  expect(screen.getByText("At latest")).toBeTruthy();
  view.stage.scrollTop = 1000;
  fireEvent.scroll(view.stage);
  expect(screen.getByText("Jump to latest")).toBeTruthy();
});

it("resumes following when a scrollbar gesture ends at the bottom without a scroll event", () => {
  const view = setup();
  fireEvent.pointerDown(view.stage);
  fireEvent.pointerUp(view.stage);
  view.grow(300);
  view.resize();
  expect(view.stage.scrollTop).toBe(1800);
  expect(screen.getByText("At latest")).toBeTruthy();
});

it("updates the jump from the actual distance when layout changes without a reader scroll", () => {
  const view = setup();
  view.readAt(1200);
  expect(screen.getByText("Jump to latest")).toBeTruthy();
  view.grow(-250);
  view.resize();
  expect(screen.getByText("At latest")).toBeTruthy();
});
