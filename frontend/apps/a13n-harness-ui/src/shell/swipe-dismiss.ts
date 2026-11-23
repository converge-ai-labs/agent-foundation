import { useCallback, useLayoutEffect, useRef } from "react";

const SLOP = 10;

/** Lets a touch drag a left-edge drawer back off screen to dismiss it.
 * Vertical drags keep scrolling the drawer; a drag never activates a row. */
export function useSwipeDismiss(onDismiss: () => void) {
  // Pointer listeners outlive renders; read the latest callback when they fire.
  const latest = useRef(onDismiss);
  useLayoutEffect(() => {
    latest.current = onDismiss;
  });
  return useCallback((popup: HTMLElement | null) => {
    if (!popup) return;
    let start: { id: number; x: number; y: number; time: number } | undefined;
    let dragging = false;
    let dragged = false;
    const settle = () => {
      popup.style.removeProperty("translate");
      popup.style.removeProperty("transition");
    };
    const down = (event: PointerEvent) => {
      if (event.pointerType !== "touch" || !event.isPrimary) return;
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
      if (!dragging) {
        if (Math.abs(dy) > SLOP && Math.abs(dy) >= Math.abs(dx)) {
          start = undefined;
          return;
        }
        if (dx > -SLOP || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        dragging = dragged = true;
        popup.style.transition = "none";
      }
      popup.style.translate = `${Math.min(0, dx)}px 0`;
    };
    const end = (event: PointerEvent) => {
      if (!start || event.pointerId !== start.id) return;
      const dx = Math.min(0, event.clientX - start.x);
      const velocity = dx / Math.max(1, event.timeStamp - start.time);
      start = undefined;
      if (!dragging) return;
      dragging = false;
      popup.style.transition = "translate 160ms ease-out";
      if (
        event.type === "pointerup" &&
        (dx < -popup.offsetWidth / 3 || (velocity < -0.5 && dx < -32))
      ) {
        popup.style.translate = "-100% 0";
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
  }, []);
}
