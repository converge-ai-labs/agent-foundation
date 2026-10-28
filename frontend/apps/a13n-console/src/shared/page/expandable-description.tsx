import { Button } from "a13n-ui";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./page.module.css";

export function ExpandableDescription({ text }: { text: string }) {
  const { t } = useTranslation();
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [truncated, setTruncated] = useState(false);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || expanded) return;
    const measure = () =>
      setTruncated(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [text, expanded]);

  return (
    <div className={styles.expandableDescription}>
      <span
        ref={ref}
        id={id}
        className={styles.subtitleText}
        data-expanded={expanded}
      >
        {text}
      </span>
      {(truncated || expanded) && (
        <Button
          variant="link"
          size="xs"
          className="px-0"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => setExpanded(!expanded)}
        >
          {t(expanded ? "Collapse description" : "Expand description")}
        </Button>
      )}
    </div>
  );
}
