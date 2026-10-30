import { useEffect, useRef, useState, type ReactNode } from "react";
import { readPreference, writePreference } from "./preferences";
import styles from "./sidebar.module.css";

const DEFAULT_WIDTH = 220;
const MIN_WIDTH = 200;
const MAX_WIDTH = 360;
const MIN_CONTENT_WIDTH = 480;

function clampWidth(width: number, maximum = MAX_WIDTH) {
  return Math.round(Math.max(MIN_WIDTH, Math.min(maximum, width)));
}

export function WorkbenchSidebar({
  collapsed,
  children,
}: {
  collapsed: boolean;
  children: ReactNode;
}) {
  const [preferredWidth, setPreferredWidth] = useState(() => {
    const saved = Number(
      readPreference("sidebar-width", String(DEFAULT_WIDTH)),
    );
    return Number.isFinite(saved) && saved > 0
      ? clampWidth(saved)
      : DEFAULT_WIDTH;
  });
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{
    pointerId: number;
    x: number;
    width: number;
    preferredWidth: number;
  } | null>(null);
  const maximum = Math.max(
    MIN_WIDTH,
    Math.min(MAX_WIDTH, viewportWidth - MIN_CONTENT_WIDTH),
  );
  const width = clampWidth(preferredWidth, maximum);

  useEffect(() => {
    const resize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  useEffect(() => {
    if (!resizing) writePreference("sidebar-width", String(preferredWidth));
  }, [preferredWidth, resizing]);

  const finish = (cancel = false) => {
    if (!drag.current) return;
    if (cancel) setPreferredWidth(drag.current.preferredWidth);
    drag.current = null;
    setResizing(false);
  };

  return (
    <aside
      id="workbench-sidebar"
      className={styles.sidebar}
      hidden={collapsed}
      style={{ width }}
      aria-label="Workbench navigation"
    >
      {children}
      <div
        className={styles.resizeHandle}
        data-resizing={resizing || undefined}
        role="separator"
        aria-label="Resize navigation"
        aria-description="Drag or use arrow keys to resize. Double-click to reset."
        aria-controls="workbench-sidebar"
        aria-orientation="vertical"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={maximum}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels`}
        tabIndex={0}
        onPointerDown={(event) => {
          if (event.button !== 0 || !event.isPrimary || drag.current) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {
            pointerId: event.pointerId,
            x: event.clientX,
            width,
            preferredWidth,
          };
          setResizing(true);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start || event.pointerId !== start.pointerId) return;
          setPreferredWidth(
            clampWidth(start.width + event.clientX - start.x, maximum),
          );
        }}
        onPointerUp={(event) => {
          if (drag.current?.pointerId !== event.pointerId) return;
          finish();
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={(event) => {
          if (drag.current?.pointerId === event.pointerId) finish(true);
        }}
        onLostPointerCapture={(event) => {
          if (drag.current?.pointerId === event.pointerId) finish();
        }}
        onDoubleClick={() => setPreferredWidth(DEFAULT_WIDTH)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && drag.current) {
            event.preventDefault();
            const { pointerId } = drag.current;
            finish(true);
            event.currentTarget.releasePointerCapture(pointerId);
            return;
          }
          if (drag.current) return;
          const next = {
            ArrowLeft: width - 8,
            ArrowRight: width + 8,
            Home: MIN_WIDTH,
            End: maximum,
          }[event.key];
          if (next === undefined) return;
          event.preventDefault();
          setPreferredWidth(clampWidth(next, maximum));
        }}
      >
        {resizing && <div className={styles.dragShield} />}
      </div>
    </aside>
  );
}
