// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSwipeDismiss } from "./swipe-dismiss";

function Drawer({
  onDismiss,
  onRow,
}: {
  onDismiss: () => void;
  onRow: () => void;
}) {
  const ref = useSwipeDismiss(onDismiss);
  return (
    <div ref={ref} data-testid="drawer">
      <button type="button" onClick={onRow}>
        Row
      </button>
    </div>
  );
}

function setup() {
  const onDismiss = vi.fn();
  const onRow = vi.fn();
  render(<Drawer onDismiss={onDismiss} onRow={onRow} />);
  const drawer = screen.getByTestId("drawer");
  Object.defineProperty(drawer, "offsetWidth", { value: 300 });
  return { drawer, row: screen.getByRole("button"), onDismiss, onRow };
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
