import { useEffect, useState } from "react";

const interactive =
  "input, textarea, select, button, a, [contenteditable]:not([contenteditable=false]), [role=slider], [role=combobox], [data-a13n-no-drag]";

/** A product opts its ModalFrame into sheet gestures with --a13n-sheet: 1. */
export function useSheetDrag({ dismissible = true } = {}) {
  const [element, ref] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!element) return;
    const viewport = element.parentElement;
    const backdrop = viewport?.previousElementSibling as HTMLElement | null;
    let gesture: {
      x: number;
      y: number;
      lastY: number;
      time: number;
      velocity: number;
      offset: number;
      base: number;
      header: boolean;
      claimed: boolean;
    } | null = null;
    const enabled = () =>
      getComputedStyle(element).getPropertyValue("--a13n-sheet").trim() === "1";
    const closeButton = () => {
      if (!dismissible || element.getAttribute("aria-busy") === "true")
        return null;
      const button = element.querySelector<HTMLButtonElement>(
        ":scope > [data-a13n-modal-close]",
      );
      return button &&
        !button.disabled &&
        button.getAttribute("aria-disabled") !== "true" &&
        !button.hidden &&
        !button.closest("[hidden], [inert]") &&
        getComputedStyle(button).display !== "none" &&
        getComputedStyle(button).visibility !== "hidden"
        ? button
        : null;
    };
    const paint = (offset: number, dragging: boolean) => {
      element.toggleAttribute("data-sheet-dragging", dragging);
      element.style.setProperty("--a13n-sheet-offset", `${offset}px`);
      if (backdrop) {
        backdrop.toggleAttribute("data-sheet-dragging", dragging);
        backdrop.style.setProperty(
          "--a13n-sheet-backdrop-opacity",
          `${Math.max(0, 1 - Math.max(0, offset) / Math.max(1, element.getBoundingClientRect().height))}`,
        );
      }
    };
    const settle = () => {
      gesture = null;
      paint(0, false);
    };
    const blocked = (target: HTMLElement) => {
      const selection = window.getSelection();
      // Only a text selection under the finger claims the gesture; a selection
      // left inside a field elsewhere in the sheet must not disable dragging.
      if (
        target.closest(interactive) ||
        (selection?.type === "Range" &&
          selection.rangeCount > 0 &&
          selection.getRangeAt(0).intersectsNode(target))
      )
        return true;
      for (
        let node: HTMLElement | null = target;
        node && node !== element;
        node = node.parentElement
      ) {
        const style = getComputedStyle(node);
        if (
          node.scrollWidth > node.clientWidth + 1 &&
          /auto|scroll/.test(style.overflowX)
        )
          return true;
        if (node.scrollTop > 0 && /auto|scroll/.test(style.overflowY))
          return true;
      }
      return false;
    };
    const start = (event: TouchEvent) => {
      if (
        !enabled() ||
        event.touches.length !== 1 ||
        element.hasAttribute("data-nested-dialog-open")
      )
        return;
      const target = event.target as HTMLElement;
      if (blocked(target)) return;
      const touch = event.touches[0]!;
      const base =
        parseFloat(
          getComputedStyle(element).translate.split(" ").at(-1) ?? "0",
        ) || 0;
      gesture = {
        x: touch.clientX,
        y: touch.clientY,
        lastY: touch.clientY,
        time: event.timeStamp,
        velocity: 0,
        offset: base,
        base,
        header: !!target.closest("[data-slot=dialog-header]"),
        claimed: false,
      };
      // Freeze a settling sheet at its current visual position, not its old target.
      paint(base, true);
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      if (event.touches.length !== 1 || !enabled()) {
        settle();
        return;
      }
      const touch = event.touches[0]!;
      const dx = touch.clientX - gesture.x;
      const dy = touch.clientY - gesture.y;
      if (!gesture.claimed) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (Math.abs(dx) > Math.abs(dy) || (!gesture.header && dy < 0)) {
          settle();
          return;
        }
        gesture.claimed = true;
      }
      if (!event.cancelable) {
        settle();
        return;
      }
      event.preventDefault();
      const elapsed = event.timeStamp - gesture.time;
      if (elapsed > 0)
        gesture.velocity = (touch.clientY - gesture.lastY) / elapsed;
      gesture.lastY = touch.clientY;
      gesture.time = event.timeStamp;
      const offset = gesture.base + dy;
      gesture.offset = offset < 0 || !closeButton() ? offset * 0.2 : offset;
      paint(gesture.offset, true);
    };
    const end = (event: TouchEvent) => {
      if (!gesture) return;
      const { claimed, offset, velocity, time } = gesture;
      const height = element.getBoundingClientRect().height;
      const dismiss =
        claimed &&
        (offset > Math.min(160, height * 0.3) ||
          (offset > 24 && velocity > 0.65 && event.timeStamp - time < 100));
      gesture = null;
      if (dismiss) closeButton()?.click();
      settle();
    };
    const suppressClick = (event: MouseEvent) => {
      if (gesture?.claimed) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    element.addEventListener("touchstart", start, { passive: true });
    element.addEventListener("touchmove", move, { passive: false });
    element.addEventListener("touchend", end);
    element.addEventListener("touchcancel", settle);
    element.addEventListener("click", suppressClick, true);
    window.addEventListener("resize", settle);
    window.visualViewport?.addEventListener("resize", settle);
    return () => {
      element.removeEventListener("touchstart", start);
      element.removeEventListener("touchmove", move);
      element.removeEventListener("touchend", end);
      element.removeEventListener("touchcancel", settle);
      element.removeEventListener("click", suppressClick, true);
      window.removeEventListener("resize", settle);
      window.visualViewport?.removeEventListener("resize", settle);
      element.style.removeProperty("--a13n-sheet-offset");
      backdrop?.style.removeProperty("--a13n-sheet-backdrop-opacity");
      backdrop?.removeAttribute("data-sheet-dragging");
    };
  }, [element, dismissible]);
  return ref;
}
