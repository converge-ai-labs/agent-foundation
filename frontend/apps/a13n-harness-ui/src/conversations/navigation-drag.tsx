import {
  createContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { ChatCircle, Folder } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import styles from "./navigation-drag.module.css";

type Thread = Schema<"ThreadSummary">;
export type DragSource =
  | { kind: "project"; id: string; label: string }
  | { kind: "thread"; id: string; label: string; thread: Thread };
export type DropTarget = {
  group: string;
  id: string;
  edge: "before" | "after" | "inside";
  peers: string[];
};
type Options = {
  drop: (source: DragSource, target: DropTarget) => void;
  expand: (group: string) => void;
};
export const NavigationDragContext = createContext<{
  begin: (event: PointerEvent<HTMLElement>, source: DragSource) => void;
  moving: string | undefined;
  moveThread: (thread: Thread, element: HTMLElement, delta: number) => void;
} | null>(null);

// Keep the list stationary during pointer movement. Only the insertion line and
// small preview move; the keyed rows are reordered once, when the pointer lifts.
export function useNavigationDrag(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const [source, setSource] = useState<DragSource>();
  const ghost = useRef<HTMLDivElement>(null);
  const line = useRef<HTMLDivElement>(null);
  const cleanup = useRef<(() => void) | undefined>(undefined);
  const suppressUntil = useRef(0);
  const cancel = useCallback(() => cleanup.current?.(), []);
  useEffect(() => {
    const click = (event: MouseEvent) => {
      if (event.detail && performance.now() < suppressUntil.current) {
        event.preventDefault();
        event.stopPropagation();
        suppressUntil.current = 0;
      }
    };
    document.addEventListener("click", click, true);
    return () => {
      cancel();
      document.removeEventListener("click", click, true);
    };
  }, [cancel]);

  const begin = (event: PointerEvent<HTMLElement>, item: DragSource) => {
    // Touch keeps native vertical scrolling; menus and keyboard offer ordering.
    if (event.button !== 0 || event.pointerType === "touch") return;
    cancel();
    suppressUntil.current = 0;
    const element = event.currentTarget;
    const scroller = element.closest<HTMLElement>("[data-project-scroll]");
    const origin = element.closest<HTMLElement>("[data-nav-group]");
    if (!scroller || !origin) return;
    const bucket =
      element.closest<HTMLElement>("[data-nav-bucket]")?.dataset.navBucket;
    const originGroup = origin.dataset.navGroup!;
    let x = event.clientX,
      y = event.clientY;
    const startX = x,
      startY = y,
      pointerId = event.pointerId;
    let started = false,
      frame = 0,
      previousTime = 0;
    let target: DropTarget | undefined;
    let highlighted: HTMLElement | undefined;
    let hovering = "",
      hoverSince = 0;

    const finish = () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", abort);
      window.removeEventListener("blur", abort);
      window.removeEventListener("keydown", key);
      scroller.removeEventListener("lostpointercapture", abort);
      if (scroller.hasPointerCapture?.(pointerId))
        scroller.releasePointerCapture(pointerId);
      highlighted?.removeAttribute("data-nav-drop");
      scroller.removeAttribute("data-nav-dragging");
      if (line.current) line.current.hidden = true;
      if (ghost.current) ghost.current.hidden = true;
      if (started) suppressUntil.current = performance.now() + 400;
      cleanup.current = undefined;
      setSource(undefined);
    };
    const abort = () => finish();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish();
      }
    };
    const inspect = (time: number) => {
      highlighted?.removeAttribute("data-nav-drop");
      highlighted = undefined;
      target = undefined;
      if (line.current) line.current.hidden = true;
      const bounds = scroller.getBoundingClientRect();
      const inside =
        x >= bounds.left &&
        x <= bounds.right &&
        y >= bounds.top &&
        y <= bounds.bottom;
      if (ghost.current) {
        ghost.current.hidden = false;
        ghost.current.style.transform = `translate(${Math.min(x + 12, window.innerWidth - 224)}px, ${Math.min(y + 12, window.innerHeight - 42)}px)`;
      }
      if (!inside) {
        hovering = "";
        return;
      }
      const hit = document.elementFromPoint(x, y);
      const group = hit?.closest<HTMLElement>("[data-nav-group]");
      if (!group || !scroller.contains(group)) return;
      const groupId = group.dataset.navGroup!;
      let targetElement: HTMLElement | undefined;
      if (item.kind === "project") {
        if (!group.dataset.projectKey || groupId === item.id) return;
        targetElement = group;
        const rect = group.getBoundingClientRect();
        target = {
          group: groupId,
          id: groupId,
          edge: y < rect.top + rect.height / 2 ? "before" : "after",
          peers: [],
        };
      } else if (groupId !== originGroup) {
        // Coordinators and their workers retain their owning Project contract.
        if (item.thread.role !== "ordinary" || groupId === "@unavailable")
          return;
        const heading = group.querySelector<HTMLElement>("[data-nav-heading]");
        if (!heading) return;
        highlighted = heading;
        heading.setAttribute("data-nav-drop", "inside");
        target = { group: groupId, id: groupId, edge: "inside", peers: [] };
        if (hovering !== groupId) {
          hovering = groupId;
          hoverSince = time;
        }
        if (
          time - hoverSince > 650 &&
          heading.querySelector('[aria-expanded="false"]')
        ) {
          latest.current.expand(groupId);
          hoverSince = Infinity;
        }
      } else {
        hovering = "";
        const row = hit?.closest<HTMLElement>("[data-nav-thread]");
        if (
          !row ||
          row.dataset.navThread === item.id ||
          row.closest<HTMLElement>("[data-nav-bucket]")?.dataset.navBucket !==
            bucket
        )
          return;
        targetElement = row;
        const rect = row.getBoundingClientRect();
        target = {
          group: groupId,
          id: row.dataset.navThread!,
          edge: y < rect.top + rect.height / 2 ? "before" : "after",
          peers: Array.from(
            group.querySelectorAll<HTMLElement>("[data-nav-thread]"),
          )
            .filter(
              (node) =>
                !node.closest("[hidden]") &&
                node.closest<HTMLElement>("[data-nav-bucket]")?.dataset
                  .navBucket === bucket,
            )
            .map((node) => node.dataset.navThread!),
        };
      }
      if (targetElement && target && line.current) {
        const rect = targetElement.getBoundingClientRect();
        const top = target.edge === "before" ? rect.top : rect.bottom;
        if (top >= bounds.top && top <= bounds.bottom) {
          line.current.hidden = false;
          line.current.style.transform = `translate(${rect.left + 4}px, ${top - 1}px)`;
          line.current.style.width = `${rect.width - 8}px`;
        }
      }
    };
    const tick = (time: number) => {
      if (!element.isConnected) {
        finish();
        return;
      }
      const bounds = scroller.getBoundingClientRect();
      const elapsed = Math.min(time - (previousTime || time), 32);
      previousTime = time;
      if (
        x >= bounds.left &&
        x <= bounds.right &&
        y >= bounds.top &&
        y <= bounds.bottom
      ) {
        const speed =
          y < bounds.top + 40
            ? -Math.min(1, (bounds.top + 40 - y) / 40)
            : y > bounds.bottom - 40
              ? Math.min(1, (y - bounds.bottom + 40) / 40)
              : 0;
        scroller.scrollTop += speed * elapsed * 0.5;
      }
      inspect(time);
      frame = requestAnimationFrame(tick);
    };
    const move = (e: globalThis.PointerEvent) => {
      if (e.pointerId !== pointerId) return;
      if (!(e.buttons & 1)) {
        finish();
        return;
      }
      x = e.clientX;
      y = e.clientY;
      if (!started && Math.hypot(x - startX, y - startY) < 6) return;
      e.preventDefault();
      if (!started) {
        started = true;
        scroller.setPointerCapture(pointerId);
        scroller.setAttribute("data-nav-dragging", "true");
        setSource(item);
        frame = requestAnimationFrame(tick);
      }
      inspect(performance.now());
    };
    const up = (e: globalThis.PointerEvent) => {
      if (e.pointerId !== pointerId) return;
      if (started) {
        x = e.clientX;
        y = e.clientY;
        inspect(performance.now());
      }
      const accepted = started ? target : undefined;
      finish();
      if (accepted) latest.current.drop(item, accepted);
    };
    cleanup.current = finish;
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", abort);
    window.addEventListener("blur", abort);
    window.addEventListener("keydown", key);
    scroller.addEventListener("lostpointercapture", abort);
  };
  return {
    begin,
    cancel,
    source,
    overlay: createPortal(
      <>
        <div ref={line} hidden className={styles.insertion} />
        <div
          ref={ghost}
          hidden={!source}
          className={styles.preview}
          aria-hidden="true"
        >
          {source?.kind === "project" ? <Folder /> : <ChatCircle />}
          <span>{source?.label}</span>
        </div>
      </>,
      document.body,
    ),
  };
}
