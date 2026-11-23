import { useEffect, useState, type CSSProperties, type RefObject } from "react";
import styles from "./conversation.module.css";

/**
 * Optical fades over a reader's top and bottom edges. Given the reader, the
 * fades expose its scrollbar width so narrow layouts can cover the whole
 * text column while keeping a classic scrollbar clear; overlay scrollbars
 * measure zero.
 */
export function ReaderFades({
  reader,
}: {
  reader?: RefObject<HTMLElement | null>;
}) {
  const [scrollbar, setScrollbar] = useState<number>();
  useEffect(() => {
    const element = reader?.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    // A classic scrollbar appearing or leaving resizes the content box.
    const observer = new ResizeObserver(() =>
      setScrollbar(element.offsetWidth - element.clientWidth),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [reader]);
  const style =
    scrollbar === undefined
      ? undefined
      : ({ "--reader-scrollbar": `${scrollbar}px` } as CSSProperties);
  return (
    <>
      <div
        className={styles.readerFade}
        data-edge="top"
        aria-hidden="true"
        style={style}
      />
      <div
        className={styles.readerFade}
        data-edge="bottom"
        aria-hidden="true"
        style={style}
      />
    </>
  );
}
