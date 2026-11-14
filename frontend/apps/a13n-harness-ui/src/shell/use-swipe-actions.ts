import {
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type CSSProperties,
} from "react";

// Shared across mounted lists: exposing another row always closes the previous one.
let closeOpenRow: (() => void) | undefined;

/** Put trailing action buttons behind contentStyle inside the rootRef container.
 * Keep the ordinary menu as the keyboard alternative. Width is the total action rail.
 * Full swipe is opt-in; callers retain confirmation/undo ownership for destructive actions.
 */
export function useSwipeActions({
  actionWidth,
  onFullSwipe,
  disabled = false,
}: {
  actionWidth: number;
  onFullSwipe?: () => void;
  disabled?: boolean;
}) {
  const fullSwipe = useEffectEvent(() => onFullSwipe?.());
  const fullSwipeEnabled = !!onFullSwipe;
  const [root, rootRef] = useState<HTMLElement | null>(null);
  const [offset, setOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const offsetRef = useRef(0);
  const suppressUntil = useRef(0);
  const change = useCallback((value: number) => {
    offsetRef.current = value;
    setOffset(value);
  }, []);
  const close = useCallback(() => {
    change(0);
    setDragging(false);
  }, [change]);
  useEffect(() => {
    if (!root || disabled || actionWidth <= 0) {
      close();
      return;
    }
    let gesture: {
      x: number;
      y: number;
      base: number;
      claimed: boolean;
    } | null = null;
    const start = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      const target = event.target as HTMLElement;
      if (
        target.closest(
          "input, textarea, select, [contenteditable], [data-swipe-action], [data-a13n-no-swipe]",
        ) ||
        window.getSelection()?.type === "Range"
      )
        return;
      const touch = event.touches[0]!;
      gesture = {
        x: touch.clientX,
        y: touch.clientY,
        base: offsetRef.current,
        claimed: false,
      };
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      if (event.touches.length !== 1) {
        gesture = null;
        close();
        return;
      }
      const touch = event.touches[0]!;
      const dx = touch.clientX - gesture.x;
      const dy = touch.clientY - gesture.y;
      if (!gesture.claimed) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 10) return;
        if (Math.abs(dy) >= Math.abs(dx) || (dx > 0 && gesture.base === 0)) {
          gesture = null;
          return;
        }
        closeOpenRow?.();
        closeOpenRow = close;
        gesture.claimed = true;
        setDragging(true);
      }
      if (!event.cancelable) {
        gesture = null;
        close();
        return;
      }
      event.preventDefault();
      const distance = Math.max(0, -gesture.base - dx);
      const limit = fullSwipeEnabled
        ? Math.max(actionWidth, root.clientWidth * 0.85)
        : actionWidth;
      change(-Math.min(limit + Math.max(0, distance - limit) * 0.15, distance));
    };
    const end = () => {
      if (!gesture) return;
      const claimed = gesture.claimed;
      gesture = null;
      setDragging(false);
      if (!claimed) return;
      suppressUntil.current = Date.now() + 700;
      const distance = -offsetRef.current;
      if (
        fullSwipeEnabled &&
        distance > Math.max(actionWidth * 1.4, root.clientWidth * 0.65)
      ) {
        close();
        fullSwipe();
      } else change(distance > actionWidth * 0.4 ? -actionWidth : 0);
    };
    const cancel = () => {
      gesture = null;
      close();
    };
    const outside = (event: Event) => {
      if (!root.contains(event.target as Node)) close();
    };
    const scroll = cancel;
    const click = (event: MouseEvent) => {
      if (
        Date.now() < suppressUntil.current &&
        !(event.target as HTMLElement).closest("[data-swipe-action]")
      ) {
        event.preventDefault();
        event.stopPropagation();
      } else if (
        offsetRef.current &&
        !(event.target as HTMLElement).closest("[data-swipe-action]")
      ) {
        event.preventDefault();
        event.stopPropagation();
        close();
      }
    };
    root.addEventListener("touchstart", start, { passive: true });
    root.addEventListener("touchmove", move, { passive: false });
    root.addEventListener("touchend", end);
    root.addEventListener("touchcancel", cancel);
    root.addEventListener("click", click, true);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("scroll", scroll, true);
    return () => {
      root.removeEventListener("touchstart", start);
      root.removeEventListener("touchmove", move);
      root.removeEventListener("touchend", end);
      root.removeEventListener("touchcancel", cancel);
      root.removeEventListener("click", click, true);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("scroll", scroll, true);
      if (closeOpenRow === close) closeOpenRow = undefined;
    };
  }, [root, actionWidth, fullSwipeEnabled, disabled, close, change]);
  return {
    rootRef,
    open: offset < 0,
    close,
    contentStyle: {
      transform: `translateX(${offset}px)`,
      transition: dragging
        ? "none"
        : "transform var(--a13n-duration-spring-snappy) var(--a13n-ease-spring-snappy)",
      touchAction: "pan-y",
    } satisfies CSSProperties,
    actionsProps: {
      inert: offset === 0,
      "aria-hidden": offset === 0,
      "data-swipe-action": "",
    },
  };
}
