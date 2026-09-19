import { XIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import styles from "./page.module.css";

const MIN_WIDTH = 360;
const MAX_WIDTH = 760;

/**
 * Side panel: the inspector anatomy. A header with the resource identity and
 * its actions, a scrollable body, and an optional drag handle at the left edge.
 * Details that belong to a row open here instead of in a modal so the
 * collection behind them stays readable. `inline` places the same anatomy in a
 * layout slot the owner sizes, so the content beside it is never covered.
 */
export function Panel({
  open,
  title,
  actions,
  tabs,
  onClose,
  resizable = true,
  defaultWidth = 420,
  width,
  onWidthChange,
  inline = false,
  label,
  children,
}: {
  open: boolean;
  title: ReactNode;
  actions?: ReactNode;
  tabs?: ReactNode;
  onClose: () => void;
  resizable?: boolean;
  defaultWidth?: number;
  /** Controlled width in pixels; the panel keeps its own when omitted. */
  width?: number;
  onWidthChange?: (width: number) => void;
  /** Fills the surrounding layout slot instead of floating over the page. */
  inline?: boolean;
  label?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [ownWidth, setOwnWidth] = useState(defaultWidth);
  const current = width ?? ownWidth;
  const dragging = useRef(false);
  const resize = useCallback(
    (next: number) => {
      const bounded = Math.round(
        Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)),
      );
      setOwnWidth(bounded);
      onWidthChange?.(bounded);
    },
    [onWidthChange],
  );
  const onPointerDown = useCallback((event: React.PointerEvent) => {
    event.preventDefault();
    dragging.current = true;
  }, []);
  useEffect(() => {
    if (!resizable) return;
    const move = (event: PointerEvent) => {
      if (!dragging.current) return;
      resize(window.innerWidth - event.clientX);
    };
    const up = () => {
      dragging.current = false;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [resizable, resize]);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside
      className={`${styles.panel} ${inline ? styles.panelInline : ""}`}
      style={inline ? undefined : { width: current }}
      aria-label={label ?? (typeof title === "string" ? title : t("Details"))}
    >
      {resizable && (
        <div
          className={styles.panelResizer}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("Resize panel")}
          aria-valuenow={current}
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={MAX_WIDTH}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            const step = event.shiftKey ? 64 : 16;
            resize(current + (event.key === "ArrowLeft" ? step : -step));
          }}
        />
      )}
      <header className={styles.panelHeader}>
        <div className={styles.panelTitle}>{title}</div>
        <div className={styles.panelActions}>
          {actions}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("Close")}
            title={t("Close")}
            onClick={onClose}
          >
            <XIcon size={14} />
          </Button>
        </div>
      </header>
      {tabs}
      <div className={`a13n-scrollbar ${styles.panelBody}`}>{children}</div>
    </aside>
  );
}
