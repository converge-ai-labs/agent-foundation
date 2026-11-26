// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSwipeDismiss } from "./swipe-dismiss";

function Drawer({
  onDismiss,
  onRow,
  edge = "left",
}: {
  onDismiss: () => void;
  onRow: () => void;
  edge?: "left" | "right";
}) {
  const ref = useSwipeDismiss(onDismiss, edge);
  return (
    <div ref={ref} data-testid="drawer">
      <button type="button" onClick={onRow}>
        Row
      </button>
      <div
        data-testid="scroller"
        className="cm-scroller"
        style={{ overflowX: "auto" }}
      >
        <span data-testid="code">wide code</span>
      </div>
    </div>
  );
}

function setup(edge: "left" | "right" = "left") {
  const onDismiss = vi.fn();
  const onRow = vi.fn();
  render(<Drawer onDismiss={onDismiss} onRow={onRow} edge={edge} />);
  const drawer = screen.getByTestId("drawer");
  Object.defineProperty(drawer, "offsetWidth", { value: 300 });
  return {
    drawer,
    row: screen.getByRole("button"),
    scroller: screen.getByTestId("scroller"),
    code: screen.getByTestId("code"),
    onDismiss,
    onRow,
  };
}

function touch(
  target: Element,
  type: string,
  clientX: number,
  clientY = 100,
  timeStamp = 0,
) {
  const event = new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    pointerId: 1,
    pointerType: "touch",
    isPrimary: true,
    clientX,
    clientY,
  });
  Object.defineProperty(event, "timeStamp", { value: timeStamp });
  fireEvent(target, event);
}

afterEach(cleanup);

it("follows a leftward touch drag and dismisses after a decisive swipe", () => {
  const { drawer, row, onDismiss, onRow } = setup();
  touch(row, "pointerdown", 250, 100, 0);
  touch(row, "pointermove", 200, 102, 50);
  expect(drawer.style.translate).toBe("-50px 0");
  touch(row, "pointermove", 100, 104, 100);
  touch(row, "pointerup", 100, 104, 400);
  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(drawer.style.translate).toBe("-100% 0");
  fireEvent.click(row);
  expect(onRow).not.toHaveBeenCalled();
});

it("snaps back after a short slow drag", () => {
  const { drawer, row, onDismiss } = setup();
  touch(row, "pointerdown", 250, 100, 0);
  touch(row, "pointermove", 220, 100, 200);
  touch(row, "pointerup", 220, 100, 600);
  expect(onDismiss).not.toHaveBeenCalled();
  expect(drawer.style.translate).toBe("0px 0");
});

it("leaves vertical scrolling, rightward drags and taps alone", () => {
  const { drawer, row, onDismiss, onRow } = setup();
  touch(row, "pointerdown", 200, 100);
  touch(row, "pointermove", 190, 160);
  touch(row, "pointerup", 120, 200);
  touch(row, "pointerdown", 100, 100);
  touch(row, "pointermove", 200, 100);
  touch(row, "pointerup", 200, 100);
  expect(drawer.style.translate).toBe("");
  expect(onDismiss).not.toHaveBeenCalled();
  fireEvent.click(row);
  expect(onRow).toHaveBeenCalledTimes(1);
});

it("ignores mouse drags", () => {
  const { drawer, row, onDismiss } = setup();
  for (const [type, x] of [
    ["pointerdown", 250],
    ["pointermove", 50],
    ["pointerup", 50],
  ] as const)
    fireEvent(
      row,
      new PointerEvent(type, {
        bubbles: true,
        pointerId: 2,
        pointerType: "mouse",
        isPrimary: true,
        clientX: x,
        clientY: 100,
      }),
    );
  expect(drawer.style.translate).toBe("");
  expect(onDismiss).not.toHaveBeenCalled();
});

it("dismisses a right-edge drawer on rightward touch swipe and respects horizontal code scrolling", () => {
  const { drawer, row, scroller, code, onDismiss } = setup("right");
  Object.defineProperty(scroller, "scrollWidth", {
    value: 600,
    configurable: true,
  });
  Object.defineProperty(scroller, "clientWidth", {
    value: 300,
    configurable: true,
  });
  Object.defineProperty(scroller, "scrollLeft", {
    value: 80,
    configurable: true,
  });

  // When scrolled right inside code, rightward drag belongs to horizontal scroll.
  touch(code, "pointerdown", 100, 100, 0);
  touch(code, "pointermove", 220, 100, 50);
  touch(code, "pointerup", 220, 100, 100);
  expect(drawer.style.translate).toBe("");
  expect(onDismiss).not.toHaveBeenCalled();

  // At scrollLeft === 0, a decisive rightward swipe dismisses the drawer.
  Object.defineProperty(scroller, "scrollLeft", {
    value: 0,
    configurable: true,
  });
  touch(row, "pointerdown", 50, 100, 0);
  touch(row, "pointermove", 120, 102, 50);
  expect(drawer.style.translate).toBe("70px 0");
  touch(row, "pointermove", 200, 104, 100);
  touch(row, "pointerup", 200, 104, 300);
  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(drawer.style.translate).toBe("100% 0");
});
