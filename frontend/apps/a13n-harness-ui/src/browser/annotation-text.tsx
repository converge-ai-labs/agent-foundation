import { useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Check, X } from "@phosphor-icons/react";
import type { Annotation, Bounds } from "./annotation-model";
import styles from "./annotation.module.css";

type TextMark = Extract<Annotation, { kind: "text" }>;

/** Native input owns composition and its draft; the document gets one commit. */
export function AnnotationText({
  mark,
  crop,
  scale,
  onApply,
  onCancel,
}: {
  mark: TextMark;
  crop: Bounds;
  scale: number;
  onApply: (mark: TextMark) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(mark.text);
  const input = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  useEffect(() => {
    // Canvas pointerdown mounts this input before mousedown's default focus.
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);
  const apply = () => {
    if (!composing.current) onApply({ ...mark, text: value });
  };
  return (
    <div
      className={styles.textEditor}
      style={{
        left: Math.max(
          0,
          Math.min((mark.x - crop.x) * scale, crop.width * scale - 240),
        ),
        top: Math.max(
          0,
          Math.min((mark.y - crop.y) * scale, crop.height * scale - 120),
        ),
      }}
    >
      <textarea
        ref={input}
        aria-label="Annotation text"
        value={value}
        style={{
          color: mark.color,
          fontSize: Math.max(16, mark.fontSize * mark.scaleY * scale),
        }}
        onChange={(event) => setValue(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (composing.current || event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          } else if (
            event.key === "Enter" &&
            (event.ctrlKey || event.metaKey)
          ) {
            event.preventDefault();
            apply();
          }
        }}
      />
      <div className={styles.textActions}>
        <Button
          size="icon-sm"
          aria-label="Apply text (Ctrl/⌘ Enter)"
          onClick={apply}
        >
          <Check />
        </Button>
        <Button
          size="icon-sm"
          variant="secondary"
          aria-label="Cancel text"
          onClick={onCancel}
        >
          <X />
        </Button>
      </div>
    </div>
  );
}
