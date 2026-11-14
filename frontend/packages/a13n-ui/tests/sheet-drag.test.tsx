import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useSheetDrag } from "../src/hooks/use-sheet-drag";
import { ModalFrame } from "../src/patterns/modal-frame";

function Sheet({
  close = vi.fn(),
  disabled = false,
  optedIn = true,
  dismissible = true,
}: {
  close?: () => void;
  disabled?: boolean;
  optedIn?: boolean;
  dismissible?: boolean;
}) {
  const ref = useSheetDrag({ dismissible });
  return (
    <>
      <div data-testid="backdrop" />
      <div>
        <div
          ref={ref}
          data-testid="sheet"
          style={{ "--a13n-sheet": optedIn ? 1 : 0 } as React.CSSProperties}
        >
          <div data-slot="dialog-header">Title</div>
          <div data-testid="body">
            Content
            <input aria-label="Name" />
          </div>
          <button data-a13n-modal-close disabled={disabled} onClick={close}>
            Close
          </button>
        </div>
      </div>
    </>
  );
}
function drag(target: Element, distance: number, x = 0) {
  fireEvent.touchStart(target, { touches: [{ clientX: 0, clientY: 0 }] });
  fireEvent.touchMove(target, { touches: [{ clientX: x, clientY: distance }] });
  fireEvent.touchEnd(target, { touches: [] });
}
describe("sheet gestures", () => {
  it("dismisses through the existing close action only when opted in", () => {
    const close = vi.fn();
    const { rerender } = render(<Sheet close={close} optedIn={false} />);
    drag(screen.getByText("Title"), 180);
    expect(close).not.toHaveBeenCalled();
    rerender(<Sheet close={close} />);
    drag(screen.getByText("Title"), 180);
    expect(close).toHaveBeenCalledOnce();
  });
  it("rubber-bands upward and makes the backdrop follow downward travel", () => {
    render(<Sheet />);
    const header = screen.getByText("Title");
    fireEvent.touchStart(header, { touches: [{ clientX: 0, clientY: 100 }] });
    fireEvent.touchMove(header, { touches: [{ clientX: 0, clientY: 50 }] });
    expect(
      screen.getByTestId("sheet").style.getPropertyValue("--a13n-sheet-offset"),
    ).toBe("-10px");
    fireEvent.touchMove(header, { touches: [{ clientX: 0, clientY: 150 }] });
    expect(
      Number(
        screen
          .getByTestId("backdrop")
          .style.getPropertyValue("--a13n-sheet-backdrop-opacity"),
      ),
    ).toBeLessThan(1);
    fireEvent.touchCancel(header);
    expect(
      screen.getByTestId("sheet").style.getPropertyValue("--a13n-sheet-offset"),
    ).toBe("0px");
  });
  it("does not dismiss a disabled or non-dismissible sheet", () => {
    const close = vi.fn();
    const { rerender } = render(<Sheet close={close} disabled />);
    drag(screen.getByText("Title"), 300);
    rerender(<Sheet close={close} dismissible={false} />);
    drag(screen.getByText("Title"), 300);
    expect(close).not.toHaveBeenCalled();
  });
  it("leaves inputs, horizontal gestures, text selection and scrolled readers alone", () => {
    const close = vi.fn();
    render(<Sheet close={close} />);
    drag(screen.getByRole("textbox"), 200);
    drag(screen.getByText("Title"), 20, 150);
    const body = screen.getByTestId("body");
    body.style.overflowY = "auto";
    body.scrollTop = 20;
    drag(body, 200);
    const range = document.createRange();
    range.selectNodeContents(screen.getByText("Title"));
    window.getSelection()!.addRange(range);
    drag(screen.getByText("Title"), 200);
    window.getSelection()!.removeAllRanges();
    expect(close).not.toHaveBeenCalled();
  });
  it("still drags when a selection lies elsewhere in the sheet", () => {
    const close = vi.fn();
    render(<Sheet close={close} />);
    const range = document.createRange();
    range.selectNodeContents(screen.getByTestId("body").firstChild!);
    window.getSelection()!.addRange(range);
    drag(screen.getByText("Title"), 200);
    window.getSelection()!.removeAllRanges();
    expect(close).toHaveBeenCalledOnce();
  });
  it("uses distance and recent release velocity, and cancels on viewport resize", () => {
    const close = vi.fn();
    render(<Sheet close={close} />);
    const sheet = screen.getByTestId("sheet");
    vi.spyOn(sheet, "getBoundingClientRect").mockReturnValue({
      height: 400,
    } as DOMRect);
    const header = screen.getByText("Title");
    const touch = (
      type: "touchStart" | "touchMove" | "touchEnd",
      y: number,
      time: number,
    ) => {
      const event = createEvent[type](header, {
        touches: type === "touchEnd" ? [] : [{ clientX: 0, clientY: y }],
      });
      Object.defineProperty(event, "timeStamp", { value: time });
      fireEvent(header, event);
    };
    touch("touchStart", 0, 0);
    touch("touchMove", 40, 200);
    touch("touchEnd", 40, 250);
    expect(close).not.toHaveBeenCalled();
    touch("touchStart", 0, 300);
    touch("touchMove", 40, 340);
    touch("touchEnd", 40, 350);
    expect(close).toHaveBeenCalledOnce();
    touch("touchStart", 0, 400);
    touch("touchMove", 140, 600);
    fireEvent(window, new Event("resize"));
    touch("touchEnd", 140, 650);
    expect(close).toHaveBeenCalledOnce();
  });
  it("keeps hidden/missing close controls and horizontal readers out of dismissal", () => {
    const close = vi.fn();
    render(<Sheet close={close} />);
    const button = screen.getByText("Close");
    button.hidden = true;
    drag(screen.getByText("Title"), 200);
    button.remove();
    drag(screen.getByText("Title"), 200);
    const body = screen.getByTestId("body");
    body.style.overflowX = "auto";
    Object.defineProperty(body, "scrollWidth", { value: 500 });
    Object.defineProperty(body, "clientWidth", { value: 200 });
    fireEvent.touchStart(body, { touches: [{ clientX: 0, clientY: 0 }] });
    expect(
      screen.getByTestId("sheet").hasAttribute("data-sheet-dragging"),
    ).toBe(false);
    expect(close).not.toHaveBeenCalled();
  });
  it("uses ModalFrame's normal cancellable onOpenChange path", async () => {
    const onOpenChange = vi.fn((_open: boolean, details: { cancel(): void }) =>
      details.cancel(),
    );
    render(
      <ModalFrame
        defaultOpen
        title="Form"
        closeLabel="Close"
        onOpenChange={onOpenChange}
      >
        Body
      </ModalFrame>,
    );
    const dialog = await screen.findByRole("dialog");
    (dialog as HTMLElement).style.setProperty("--a13n-sheet", "1");
    drag(screen.getByText("Form"), 180);
    expect(onOpenChange).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
