import {
  useEffect,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import type { Schema } from "../transport/client";
import styles from "./shared-pointers.module.css";

type Position = Schema<"PointerPosition">;
const anchorSelector = "[data-presence-anchor]";

export function pointerPosition(
  target: EventTarget | null,
  x: number,
  y: number,
): Position | null {
  const anchor =
    target instanceof Element
      ? target.closest<HTMLElement>(anchorSelector)
      : null;
  if (!anchor) return null;
  const rect = anchor.getBoundingClientRect();
  if (
    !rect.width ||
    !rect.height ||
    x < rect.left ||
    x > rect.right ||
    y < rect.top ||
    y > rect.bottom
  )
    return null;
  return {
    anchor: anchor.dataset.presenceAnchor!,
    x: (x - rect.left) / rect.width,
    y: (y - rect.top) / rect.height,
  };
}

export function pointerCoordinates(
  position: Position,
): { x: number; y: number } | null {
  const anchor = Array.from(
    document.querySelectorAll<HTMLElement>(anchorSelector),
  ).find((element) => element.dataset.presenceAnchor === position.anchor);
  if (!anchor) return null;
  const rect = anchor.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = rect.left + rect.width * position.x;
  const y = rect.top + rect.height * position.y;
  if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight)
    return null;
  // Do not float a pointer over clipped content, other panes, or an open dialog.
  const visible = document.elementFromPoint(x, y);
  return visible && anchor.contains(visible) ? { x, y } : null;
}

export function SharedPointers({
  socket,
  presence,
  focus,
}: {
  socket: RefObject<WebSocket | null>;
  presence: Schema<"PresenceFrame"> | null;
  focus?: Schema<"PageFocus"> | null;
}) {
  const threadId =
    focus?.target.kind === "conversation" ? focus.target.thread_id : null;
  const [frame, setFrame] = useState<Schema<"PointerFrame"> | null>(null);
  const [coordinates, setCoordinates] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const ownId = presence?.participant_id;

  useEffect(() => {
    const ws = socket.current;
    setFrame(null);
    if (!ws || !ownId || !threadId) return;
    let pending: Position | null = null;
    let published = false;
    let throttle: ReturnType<typeof setTimeout> | undefined;
    let idle: ReturnType<typeof setTimeout> | undefined;
    let stale: ReturnType<typeof setTimeout> | undefined;
    const send = (pointer: Position | null) => {
      if (ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 16384) {
        ws.send(
          JSON.stringify({
            kind: "pointer",
            target: { kind: "conversation", thread_id: threadId },
            pointer,
          } satisfies Schema<"PointerReport">),
        );
        published = pointer !== null;
      }
    };
    const clear = () => {
      clearTimeout(throttle);
      clearTimeout(idle);
      throttle = undefined;
      pending = null;
      if (published) send(null);
    };
    const receive = (event: MessageEvent) => {
      try {
        const value = JSON.parse(String(event.data)) as Schema<"PointerFrame">;
        if (value.kind !== "pointers") return;
        setFrame(value.target?.thread_id === threadId ? value : null);
        clearTimeout(stale);
        stale = setTimeout(() => setFrame(null), 6000);
      } catch {
        /* The presence connection owns malformed-frame handling. */
      }
    };
    const move = (event: PointerEvent) => {
      if (
        event.pointerType === "touch" ||
        !document.hasFocus() ||
        document.visibilityState !== "visible"
      )
        return clear();
      pending = pointerPosition(event.target, event.clientX, event.clientY);
      if (!pending) return clear();
      clearTimeout(idle);
      idle = setTimeout(clear, 5000);
      // Keep the latest position at most ten times a second, independent of React rendering.
      throttle ??= setTimeout(() => {
        throttle = undefined;
        send(pending);
      }, 100);
    };
    const leave = (event: PointerEvent) => {
      if (!event.relatedTarget) clear();
    };
    const inactive = () => {
      clear();
      setFrame(null);
    };
    ws.addEventListener("message", receive);
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerout", leave);
    document.addEventListener("visibilitychange", inactive);
    window.addEventListener("blur", inactive);
    window.addEventListener("scroll", clear, true);
    window.addEventListener("resize", clear);
    return () => {
      clear();
      clearTimeout(stale);
      ws.removeEventListener("message", receive);
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerout", leave);
      document.removeEventListener("visibilitychange", inactive);
      window.removeEventListener("blur", inactive);
      window.removeEventListener("scroll", clear, true);
      window.removeEventListener("resize", clear);
    };
  }, [socket, ownId, threadId]);

  useLayoutEffect(() => {
    if (!frame || !threadId) {
      setCoordinates({});
      return;
    }
    let scheduled = 0;
    const measure = () => {
      const next: typeof coordinates = {};
      if (frame?.target?.thread_id === threadId)
        for (const [id, position] of Object.entries(frame.pointers ?? {})) {
          const point = pointerCoordinates(position);
          if (point) next[id] = point;
        }
      setCoordinates(next);
    };
    const schedule = () => {
      cancelAnimationFrame(scheduled);
      scheduled = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    const resize = new ResizeObserver(schedule);
    document
      .querySelectorAll(anchorSelector)
      .forEach((element) => resize.observe(element));
    return () => {
      cancelAnimationFrame(scheduled);
      resize.disconnect();
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
    };
  }, [frame, threadId]);

  if (!presence || presence.closed || frame?.target?.thread_id !== threadId)
    return null;
  return (
    <div className={styles.layer} aria-hidden="true">
      {presence.participants.map((participant) => {
        const point = coordinates[participant.participant_id];
        if (
          !point ||
          participant.participant_id === ownId ||
          !participant.foreground ||
          participant.availability !== "available" ||
          participant.focus?.target.kind !== "conversation" ||
          participant.focus.target.thread_id !== threadId
        )
          return null;
        return (
          <div
            key={participant.participant_id}
            className={styles.pointer}
            style={
              {
                left: point.x,
                top: point.y,
                "--pointer-color": participant.color ?? "#64748b",
              } as CSSProperties
            }
            data-flip-x={point.x > window.innerWidth - 180 || undefined}
            data-flip-y={point.y > window.innerHeight - 55 || undefined}
          >
            <svg width="22" height="26" viewBox="0 0 22 26">
              <path d="M2 2 18 14 10 15 6 23Z" />
            </svg>
            <span>{participant.display_name?.trim() || "Anonymous"}</span>
          </div>
        );
      })}
    </div>
  );
}
