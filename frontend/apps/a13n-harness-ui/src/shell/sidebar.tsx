import { useTranslation } from "../i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Button,
  Logo,
  Sheet,
  SheetPortal,
  SheetBackdrop,
  SheetViewport,
  SheetPrimitive,
  SheetTitle,
  SheetClose,
} from "a13n-ui";
import { X } from "@phosphor-icons/react";
import { useChatSearchOpen } from "../conversations/chat-search";
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
  const { t } = useTranslation();
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
    <div
      className={styles.sidebarSlot}
      style={{ width: collapsed ? 0 : width }}
    >
      <aside
        id="workbench-sidebar"
        className={styles.sidebar}
        data-collapsed={collapsed}
        inert={collapsed}
        aria-hidden={collapsed || undefined}
        style={{ width }}
        aria-label={t("Workbench navigation")}
      >
        {children}
        <div
          className={styles.resizeHandle}
          data-resizing={resizing || undefined}
          role="separator"
          aria-label={t("Resize navigation")}
          aria-description={t(
            "Drag or use arrow keys to resize. Double-click to reset.",
          )}
          aria-controls="workbench-sidebar"
          aria-orientation="vertical"
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={maximum}
          aria-valuenow={width}
          aria-valuetext={t("{{width, number}} pixels", { width })}
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
    </div>
  );
}

// Base UI owns modality, focus restoration, Escape and background scroll locking.
// Only horizontal gestures are claimed; vertical and nested horizontal readers
// keep their native scrolling behavior.
export function MobileNavigation({
  open,
  onOpenChange,
  finalFocus,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  finalFocus: () => HTMLElement | null;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const popup = useRef<HTMLDivElement>(null);
  const searchOpen = useChatSearchOpen();
  useEffect(() => {
    if (searchOpen && open) onOpenChange(false);
  }, [searchOpen, open, onOpenChange]);
  const currentOpen = useRef(open);
  currentOpen.current = open;
  const [offset, setOffset] = useState<number | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  useEffect(() => {
    let gesture: {
      x: number;
      y: number;
      lastX: number;
      time: number;
      velocity: number;
      width: number;
      start: number;
      opening: boolean;
      claimed: boolean;
    } | null = null;
    const start = (event: TouchEvent) => {
      if (window.innerWidth > 760 || event.touches.length !== 1) return;
      const target = event.target as HTMLElement;
      const point = event.touches[0]!;
      const opening = !currentOpen.current;
      if (opening && point.clientX > 20) return;
      if (
        !opening &&
        !target.closest(
          "[data-mobile-navigation], [data-mobile-navigation-scrim], [data-mobile-navigation-viewport]",
        )
      )
        return;
      if (target.closest("[data-nested-dialog-open]")) return;
      // Do not steal a swipe from a horizontally scrolling code block or editor.
      for (
        let node: HTMLElement | null = target;
        node && node !== popup.current;
        node = node.parentElement
      ) {
        if (
          node.scrollWidth > node.clientWidth + 1 &&
          /auto|scroll/.test(getComputedStyle(node).overflowX)
        )
          return;
      }
      const width =
        popup.current?.offsetWidth ?? Math.min(300, window.innerWidth - 48);
      gesture = {
        x: point.clientX,
        y: point.clientY,
        lastX: point.clientX,
        time: performance.now(),
        velocity: 0,
        width,
        start: opening
          ? -width
          : (popup.current?.getBoundingClientRect().left ?? 0),
        opening,
        claimed: false,
      };
    };
    const move = (event: TouchEvent) => {
      if (!gesture || event.touches.length !== 1) return;
      const point = event.touches[0]!;
      const dx = point.clientX - gesture.x;
      const dy = point.clientY - gesture.y;
      if (!gesture.claimed) {
        if (Math.abs(dy) > 8 && Math.abs(dy) >= Math.abs(dx)) {
          gesture = null;
          return;
        }
        if (Math.abs(dx) < 8) return;
        if ((gesture.opening && dx < 0) || (!gesture.opening && dx > 0)) {
          gesture = null;
          return;
        }
        gesture.claimed = true;
        if (gesture.opening) onOpenChange(true);
      }
      event.preventDefault();
      const now = performance.now();
      gesture.velocity =
        (point.clientX - gesture.lastX) / Math.max(1, now - gesture.time);
      gesture.lastX = point.clientX;
      gesture.time = now;
      const next = Math.max(-gesture.width, Math.min(0, gesture.start + dx));
      setOffset(next);
      setProgress(1 + next / gesture.width);
    };
    const finish = (cancelled = false) => {
      if (!gesture) return;
      if (gesture.claimed) {
        const position = Math.max(
          -gesture.width,
          Math.min(0, gesture.start + gesture.lastX - gesture.x),
        );
        const velocity =
          performance.now() - gesture.time < 100 ? gesture.velocity : 0;
        onOpenChange(
          cancelled
            ? !gesture.opening
            : position + velocity * 180 > -gesture.width / 2,
        );
        setOffset(null);
        setProgress(null);
      }
      gesture = null;
    };
    const end = () => finish();
    const cancel = () => finish(true);
    const resize = () => {
      if (window.innerWidth > 760) {
        finish(true);
        onOpenChange(false);
      }
    };
    document.addEventListener("touchstart", start, { passive: true });
    document.addEventListener("touchmove", move, { passive: false });
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", cancel);
    window.addEventListener("resize", resize);
    return () => {
      document.removeEventListener("touchstart", start);
      document.removeEventListener("touchmove", move);
      document.removeEventListener("touchend", end);
      document.removeEventListener("touchcancel", cancel);
      window.removeEventListener("resize", resize);
    };
  }, [onOpenChange]);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPortal>
        <SheetBackdrop
          data-mobile-navigation-scrim
          className={styles.mobileBackdrop}
          data-dragging={offset !== null || undefined}
          style={progress === null ? undefined : { opacity: progress }}
        />
        <SheetViewport side="left" data-mobile-navigation-viewport>
          <SheetPrimitive.Popup
            ref={popup}
            id="workbench-navigation"
            data-mobile-navigation
            data-dragging={offset !== null || undefined}
            className={styles.mobileNavigation}
            style={
              offset === null
                ? undefined
                : { transform: `translateX(${offset}px)` }
            }
            finalFocus={() => (searchOpen ? false : finalFocus())}
          >
            <header className={styles.mobileHeader}>
              <SheetTitle className={styles.mobileBrand}>
                <Logo alt="" width={20} height={20} />
                <span>Harness</span>
              </SheetTitle>
              <SheetClose
                render={<Button variant="ghost" size="icon" />}
                aria-label={t("Close navigation")}
              >
                <X />
              </SheetClose>
            </header>
            {children}
          </SheetPrimitive.Popup>
        </SheetViewport>
      </SheetPortal>
    </Sheet>
  );
}
