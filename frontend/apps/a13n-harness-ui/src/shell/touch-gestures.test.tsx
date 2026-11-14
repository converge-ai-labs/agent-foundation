// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSwipeActions } from "./use-swipe-actions";
import { useLongPress } from "./use-long-press";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
function Row({
  name,
  fullSwipe,
  action,
}: {
  name: string;
  fullSwipe?: () => void;
  action?: () => void;
}) {
  const { rootRef, open, contentStyle, actionsProps } = useSwipeActions({
    actionWidth: 100,
    onFullSwipe: fullSwipe,
  });
  return (
    <div ref={rootRef} data-testid={name} data-open={open}>
      <div {...actionsProps}>
        <button onClick={action}>Archive {name}</button>
      </div>
      <div style={contentStyle}>{name}</div>
    </div>
  );
}
function swipe(name: string, x: number, y = 0) {
  const row = screen.getByText(name);
  fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 0 }] });
  fireEvent.touchMove(row, { touches: [{ clientX: 200 + x, clientY: y }] });
  fireEvent.touchEnd(row, { touches: [] });
}
describe("swipe actions", () => {
  it("allows a deliberate rail action immediately after swiping while suppressing foreground navigation", () => {
    const action = vi.fn();
    render(<Row name="First" action={action} />);
    swipe("First", -80);
    fireEvent.click(screen.getByRole("button", { name: "Archive First" }));
    expect(action).toHaveBeenCalledOnce();
  });
  it("lets vertical scrolling win and exposes only one row at a time", () => {
    render(
      <>
        <Row name="First" />
        <Row name="Second" />
      </>,
    );
    swipe("First", -20, 80);
    expect(screen.getByTestId("First").dataset.open).toBe("false");
    swipe("First", -80);
    expect(screen.getByTestId("First").dataset.open).toBe("true");
    swipe("Second", -80);
    expect(screen.getByTestId("First").dataset.open).toBe("false");
    expect(screen.getByTestId("Second").dataset.open).toBe("true");
    fireEvent.scroll(document);
    expect(screen.getByTestId("Second").dataset.open).toBe("false");
  });
  it("closes on outside tap and only commits a full swipe when explicitly supplied", () => {
    const fullSwipe = vi.fn();
    render(<Row name="First" fullSwipe={fullSwipe} />);
    Object.defineProperty(screen.getByTestId("First"), "clientWidth", {
      value: 200,
    });
    swipe("First", -80);
    expect(fullSwipe).not.toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    expect(screen.getByTestId("First").dataset.open).toBe("false");
    swipe("First", -180);
    expect(fullSwipe).toHaveBeenCalledOnce();
  });
});
function PressRow({ open, click }: { open: () => void; click: () => void }) {
  const { pressProps } = useLongPress(open);
  return (
    <button {...pressProps} onClick={click}>
      Hold
    </button>
  );
}
// jsdom's default PointerEvent loses pointerType; supply a minimal real event.
function pointer(target: Element, type: string, x = 0) {
  const event = new Event(type, { bubbles: true });
  Object.assign(event, {
    pointerType: "touch",
    isPrimary: true,
    clientX: x,
    clientY: 0,
  });
  fireEvent(target, event);
}
describe("long press", () => {
  it("opens the existing menu and suppresses the synthetic click", () => {
    vi.useFakeTimers();
    const open = vi.fn(),
      click = vi.fn();
    render(<PressRow open={open} click={click} />);
    const row = screen.getByText("Hold");
    pointer(row, "pointerdown");
    act(() => vi.advanceTimersByTime(450));
    expect(open).toHaveBeenCalledOnce();
    pointer(row, "pointerup");
    fireEvent.click(row);
    expect(click).not.toHaveBeenCalled();
  });
  it("cancels for movement and scroll without swallowing ordinary taps", () => {
    vi.useFakeTimers();
    const open = vi.fn(),
      click = vi.fn();
    render(<PressRow open={open} click={click} />);
    const row = screen.getByText("Hold");
    pointer(row, "pointerdown");
    pointer(row, "pointermove", 20);
    act(() => vi.advanceTimersByTime(500));
    expect(open).not.toHaveBeenCalled();
    pointer(row, "pointerdown");
    fireEvent.scroll(document);
    act(() => vi.advanceTimersByTime(500));
    expect(open).not.toHaveBeenCalled();
    pointer(row, "pointerdown");
    pointer(row, "pointerup");
    fireEvent.click(row);
    expect(click).toHaveBeenCalledOnce();
  });
});
