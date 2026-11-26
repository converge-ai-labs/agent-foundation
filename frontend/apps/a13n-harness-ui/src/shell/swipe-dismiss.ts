import { useCallback, useLayoutEffect, useRef } from "react";

const SLOP = 10;

function canScrollInSwipeDirection(
  target: EventTarget | null,
  boundary: HTMLElement,
  edge: "left" | "right",
): boolean {
  let element = target instanceof Element ? target : null;
  while (element && element !== boundary) {
    if (element.scrollWidth > element.clientWidth + 1) {
      const overflowX = getComputedStyle(element).overflowX;
      const scrollable =
        overflowX === "auto" ||
        overflowX === "scroll" ||
        element.classList.contains("cm-scroller") ||
        element.tagName === "PRE";
      if (scrollable) {
        // Right-edge dismiss drags left->right (scrolling toward scrollLeft 0);
        // left-edge dismiss drags right->left (scrolling toward max scrollLeft).
        if (edge === "right" && element.scrollLeft > 0) return true;
        if (
          edge === "left" &&
          element.scrollLeft + element.clientWidth < element.scrollWidth - 1
        )
          return true;
      }
    }
    element = element.parentElement;
  }
  return false;
}

/** Lets a touch drag a left- or right-edge drawer back off screen to dismiss it.
 * Vertical drags and horizontal scroll containers keep scrolling; a drag never activates a row. */
export function useSwipeDismiss(
  onDismiss: () => void,
  edge: "left" | "right" = "left",
) {
  // Pointer listeners outlive renders; read the latest callback when they fire.
  const latest = useRef(onDismiss);
  useLayoutEffect(() => {
    latest.current = onDismiss;
  });
  return useCallback(
    (popup: HTMLElement | null) => {
      if (!popup) return;
      const sign = edge === "right" ? 1 : -1;
      let start: { id: number; x: number; y: number; time: number } | undefined;
      let dragging = false;
      let dragged = false;
      const settle = () => {
        popup.style.removeProperty("translate");
        popup.style.removeProperty("transition");
      };
      const down = (event: PointerEvent) => {
        if (event.pointerType !== "touch" || !event.isPrimary) return;
        if (
          event.target instanceof Element &&
          event.target.closest("audio, video, input, textarea, select")
        )
          return;
        if (canScrollInSwipeDirection(event.target, popup, edge)) return;
        start = {
          id: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          time: event.timeStamp,
        };
        dragging = dragged = false;
      };
      const move = (event: PointerEvent) => {
        if (!start || event.pointerId !== start.id) return;
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        const progress = dx * sign;
        if (!dragging) {
          if (Math.abs(dy) > SLOP && Math.abs(dy) >= Math.abs(dx)) {
            start = undefined;
            return;
          }
          if (progress < SLOP || Math.abs(dx) < Math.abs(dy) * 1.5) return;
          dragging = dragged = true;
          popup.style.transition = "none";
        }
        popup.style.translate = `${Math.max(0, progress) * sign}px 0`;
      };
      const end = (event: PointerEvent) => {
        if (!start || event.pointerId !== start.id) return;
        const progress = Math.max(0, (event.clientX - start.x) * sign);
        const velocity = progress / Math.max(1, event.timeStamp - start.time);
        start = undefined;
        if (!dragging) return;
        dragging = false;
        popup.style.transition = "translate 160ms ease-out";
        if (
          event.type === "pointerup" &&
          (progress > popup.offsetWidth / 3 ||
            (velocity > 0.5 && progress > 32))
        ) {
          popup.style.translate = `${sign * 100}% 0`;
          latest.current();
        } else {
          popup.style.translate = "0px 0";
          popup.addEventListener("transitionend", settle, { once: true });
        }
      };
      // The release after a drag still produces a click on the row beneath it.
      const click = (event: MouseEvent) => {
        if (!dragged) return;
        dragged = false;
        event.preventDefault();
        event.stopPropagation();
      };
      popup.addEventListener("pointerdown", down);
      popup.addEventListener("pointermove", move);
      popup.addEventListener("pointerup", end);
      popup.addEventListener("pointercancel", end);
      popup.addEventListener("click", click, true);
      return () => {
        popup.removeEventListener("pointerdown", down);
        popup.removeEventListener("pointermove", move);
        popup.removeEventListener("pointerup", end);
        popup.removeEventListener("pointercancel", end);
        popup.removeEventListener("click", click, true);
      };
    },
    [edge],
  );
}
