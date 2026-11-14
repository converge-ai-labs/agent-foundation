import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
} from "react";

/** Opens the row's existing menu; never replaces its keyboard/overflow trigger. */
export function useLongPress(
  onOpen: () => void,
  { disabled = false, delay = 450 } = {},
) {
  const [pressed, setPressed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const suppressUntil = useRef(0);
  const fired = useRef(false);
  const cancel = () => {
    clearTimeout(timer.current);
    timer.current = undefined;
    origin.current = null;
    setPressed(false);
  };
  useEffect(() => {
    const scroll = () => cancel();
    document.addEventListener("scroll", scroll, true);
    return () => {
      clearTimeout(timer.current);
      document.removeEventListener("scroll", scroll, true);
    };
  }, []);
  useEffect(() => {
    if (disabled) cancel();
  }, [disabled]);
  const pressProps: HTMLAttributes<HTMLElement> = {
    onPointerDown(event) {
      if (!event.isPrimary) {
        cancel();
        return;
      }
      fired.current = false;
      if (
        disabled ||
        event.pointerType !== "touch" ||
        (event.target as HTMLElement).closest(
          "input, textarea, select, [contenteditable], [data-a13n-no-long-press]",
        )
      )
        return;
      const control = (event.target as HTMLElement).closest("button, a");
      if (control && control !== event.currentTarget) return;
      cancel();
      origin.current = { x: event.clientX, y: event.clientY };
      setPressed(true);
      timer.current = setTimeout(() => {
        fired.current = true;
        suppressUntil.current = Date.now() + 1000;
        setPressed(false);
        onOpen();
      }, delay);
    },
    onPointerMove(event) {
      if (
        origin.current &&
        Math.hypot(
          event.clientX - origin.current.x,
          event.clientY - origin.current.y,
        ) > 8
      )
        cancel();
    },
    onPointerUp() {
      if (fired.current) suppressUntil.current = Date.now() + 1000;
      cancel();
    },
    onPointerCancel: cancel,
    onPointerLeave: cancel,
    onClickCapture(event) {
      if (Date.now() < suppressUntil.current) {
        event.preventDefault();
        event.stopPropagation();
        suppressUntil.current = 0;
      }
    },
    onContextMenu(event) {
      if (origin.current || Date.now() < suppressUntil.current)
        event.preventDefault();
    },
    style: {
      WebkitTouchCallout: "none",
      userSelect: "none",
      scale: pressed ? "0.985" : "1",
      transition: "scale var(--a13n-duration-fast) var(--a13n-ease-out)",
      touchAction: "pan-y",
      WebkitTapHighlightColor: "transparent",
    } as CSSProperties,
  };
  return { pressProps, pressed, cancel };
}
