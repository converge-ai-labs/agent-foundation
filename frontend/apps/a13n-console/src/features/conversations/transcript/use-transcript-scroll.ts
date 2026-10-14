import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

type Mode = "following" | "reading" | "jumping";
interface ScrollController {
  followLatest: () => void;
  jumpToLatest: () => void;
  preservePosition: () => () => void;
  align: () => void;
  reflow: () => void;
}

/**
 * The only owner of transcript scrolling. Live growth follows the end; a
 * history prepend restores a visible Run before paint. Only the explicit
 * return-to-latest action animates, never layout corrections or streaming.
 */
export function useTranscriptScroll(
  content: RefObject<HTMLElement | null>,
  level?: string,
) {
  const [showJump, setShowJump] = useState(false);
  const controller = useRef<ScrollController | null>(null);
  useLayoutEffect(() => {
    const element = content.current;
    const stage = element?.closest("[data-session-stage]");
    if (!element || !(stage instanceof HTMLElement)) return;
    let mode: Mode = "following";
    let readerScrolling = false;
    const capture = () => {
      const edge = stage.getBoundingClientRect().top;
      const run = [...element.querySelectorAll<HTMLElement>("[data-run]")].find(
        (run) => run.getBoundingClientRect().bottom > edge,
      );
      return run
        ? { id: run.dataset.run, top: run.getBoundingClientRect().top }
        : undefined;
    };
    let anchor = capture();
    const atEnd = () =>
      stage.scrollHeight - stage.scrollTop - stage.clientHeight < 100;
    // Scroll intent controls following; actual geometry controls the button.
    // A wheel gesture at the boundary may never produce a scroll event.
    const updateJump = () => setShowJump(mode !== "jumping" && !atEnd());
    const move = (top: number) => stage.scrollTo({ top, behavior: "instant" });
    const setMode = (next: Mode) => {
      mode = next;
      updateJump();
    };
    const align = () => {
      if (mode === "following") move(stage.scrollHeight);
      updateJump();
    };
    const followLatest = () => {
      readerScrolling = false;
      setMode("following");
      align();
    };
    const onScroll = () => {
      if (readerScrolling && mode !== "jumping") {
        setMode(atEnd() ? "following" : "reading");
        anchor = capture();
      } else updateJump();
    };
    const onScrollEnd = () => {
      readerScrolling = false;
      // Output may have grown during the one explicit native animation.
      if (mode === "jumping" || atEnd()) followLatest();
      else updateJump();
    };
    const onGestureEnd = () => {
      // Scrollbar taps and touches can also finish without moving the stage.
      if (readerScrolling && atEnd()) followLatest();
    };
    const readerScroll = (event: Event) => {
      if (
        event instanceof KeyboardEvent &&
        (![
          "ArrowUp",
          "ArrowDown",
          "PageUp",
          "PageDown",
          "Home",
          "End",
          " ",
        ].includes(event.key) ||
          (event.target instanceof Element &&
            event.target.closest("input, textarea, [contenteditable]")))
      )
        return;
      if (event.type === "pointerdown" && event.target !== stage) return;
      if (event instanceof WheelEvent && !event.deltaY) return;
      const towardEnd =
        (event instanceof WheelEvent && event.deltaY > 0) ||
        (event instanceof KeyboardEvent &&
          (["ArrowDown", "PageDown", "End"].includes(event.key) ||
            (event.key === " " && !event.shiftKey)));
      if (towardEnd && atEnd()) {
        followLatest();
        return;
      }
      // Stop following at the gesture, before a resize can race the scroll event.
      if (mode === "jumping") move(stage.scrollTop);
      readerScrolling = true;
      setMode("reading");
      anchor = capture();
    };
    const restore = (saved: typeof anchor, changedLevel = false) => {
      if (mode === "following") align();
      else if (mode === "reading" && saved) {
        const run = [
          ...element.querySelectorAll<HTMLElement>("[data-run]"),
        ].find((run) => run.dataset.run === saved.id);
        if (!run) return;
        const bounds = run.getBoundingClientRect();
        // Chat can be much shorter than Debug. Keep the same Run in view
        // without restoring an offset beyond its newly rendered content.
        const top = changedLevel
          ? Math.max(
              saved.top,
              stage.getBoundingClientRect().top -
                Math.max(0, bounds.height - stage.clientHeight),
            )
          : saved.top;
        readerScrolling = false;
        move(stage.scrollTop + bounds.top - top);
        updateJump();
      }
      anchor = capture();
    };
    controller.current = {
      align,
      reflow: () => restore(anchor, true),
      followLatest,
      jumpToLatest() {
        if (atEnd() || matchMedia("(prefers-reduced-motion: reduce)").matches) {
          followLatest();
          return;
        }
        readerScrolling = false;
        setMode("jumping");
        stage.scrollTo({ top: stage.scrollHeight, behavior: "smooth" });
      },
      preservePosition() {
        // Capture immediately before insertion, not before its network read.
        const saved = capture();
        return () => restore(saved);
      },
    };
    const observer = new ResizeObserver(align);
    // Composer height and viewport resizing matter as much as message growth.
    observer.observe(element);
    observer.observe(stage);
    stage.addEventListener("scroll", onScroll, { passive: true });
    stage.addEventListener("scrollend", onScrollEnd);
    for (const event of ["wheel", "touchmove", "pointerdown", "keydown"])
      stage.addEventListener(event, readerScroll, { passive: true });
    for (const event of ["touchend", "pointerup", "keyup"])
      stage.addEventListener(event, onGestureEnd, { passive: true });
    align();
    return () => {
      observer.disconnect();
      stage.removeEventListener("scroll", onScroll);
      stage.removeEventListener("scrollend", onScrollEnd);
      for (const event of ["wheel", "touchmove", "pointerdown", "keydown"])
        stage.removeEventListener(event, readerScroll);
      for (const event of ["touchend", "pointerup", "keyup"])
        stage.removeEventListener(event, onGestureEnd);
      controller.current = null;
    };
  }, [content]);
  useLayoutEffect(() => controller.current?.reflow(), [level]);
  useLayoutEffect(() => controller.current?.align());
  const followLatest = useCallback(
    () => controller.current?.followLatest(),
    [],
  );
  const jumpToLatest = useCallback(
    () => controller.current?.jumpToLatest(),
    [],
  );
  const preservePosition = useCallback(
    () => controller.current?.preservePosition() ?? (() => {}),
    [],
  );
  return { showJump, followLatest, jumpToLatest, preservePosition };
}
