import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

const ORDER_KEY = "a13n-harness-ui.project-order";
const EXPANSION_KEY = "a13n-harness-ui.project-expansion";

function readPreference(key: string): unknown {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}
function savePreference(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Navigation remains usable when browser storage is unavailable. */
  }
}
export function useProjectExpansion() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    const saved = readPreference(EXPANSION_KEY);
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return {};
    return Object.fromEntries(
      Object.entries(saved).filter(([, value]) => typeof value === "boolean"),
    );
  });
  const update = useCallback(
    (id: string, open: boolean) =>
      setExpanded((current) => {
        const next = { ...current, [id]: open };
        savePreference(EXPANSION_KEY, next);
        return next;
      }),
    [],
  );
  return [expanded, update] as const;
}

export function useProjectView(projectId: string) {
  const key = `a13n-harness-ui.project-view.${projectId}`;
  const [view, setView] = useState<"lead" | "conversations">(() =>
    readPreference(key) === "lead" ? "lead" : "conversations",
  );
  return [
    view,
    (next: "lead" | "conversations") => {
      setView(next);
      savePreference(key, next);
    },
  ] as const;
}

export function orderedProjects(ids: string[], saved: string[]) {
  return [...new Set([...saved.filter((id) => ids.includes(id)), ...ids])];
}
function move(ids: string[], id: string, index: number) {
  const next = ids.filter((item) => item !== id);
  next.splice(Math.max(0, Math.min(index, next.length)), 0, id);
  return next;
}

// Presentation only: never write Project configuration or Thread metadata.
export function useProjectOrder(ids: string[]) {
  const [saved, setSaved] = useState<string[]>(() => {
    const value = readPreference(ORDER_KEY);
    return Array.isArray(value)
      ? value.filter((id): id is string => typeof id === "string")
      : [];
  });
  const [preview, setPreview] = useState<string[] | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const pointer = useRef<{
    id: string;
    x: number;
    y: number;
    started: boolean;
  } | null>(null);
  const current = orderedProjects(ids, preview ?? saved);
  const commit = (next: string[]) => {
    setSaved(next);
    savePreference(ORDER_KEY, next);
    setPreview(null);
    setMoving(null);
  };
  const cancel = useCallback(() => {
    pointer.current = null;
    setPreview(null);
    setMoving(null);
  }, []);
  const handles = useRef(new Map<string, HTMLElement>());
  useLayoutEffect(() => {
    // Moving a keyed DOM node can drop keyboard focus. Restore the active handle.
    if (moving && !pointer.current) handles.current.get(moving)?.focus();
  }, [moving, preview]);
  useEffect(() => {
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") cancel();
    };
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", cancel);
    return () => {
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", cancel);
    };
  }, [cancel]);
  const keyboard = (event: KeyboardEvent, id: string) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (moving === id) {
        commit(current);
        setAnnouncement("Project order saved in this browser.");
      } else {
        setMoving(id);
        setPreview(current);
        setAnnouncement(
          "Use Up and Down to move, Enter to save, Escape to cancel.",
        );
      }
    } else if (
      moving === id &&
      (event.key === "ArrowUp" || event.key === "ArrowDown")
    ) {
      event.preventDefault();
      const next = move(
        current,
        id,
        current.indexOf(id) + (event.key === "ArrowUp" ? -1 : 1),
      );
      setPreview(next);
      setAnnouncement(
        `Project position ${next.indexOf(id) + 1} of ${next.length}.`,
      );
    }
  };
  const pointerMove = (event: PointerEvent<HTMLElement>) => {
    const drag = pointer.current;
    if (!drag) return;
    if (
      !drag.started &&
      Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 5
    )
      return;
    drag.started = true;
    setMoving(drag.id);
    const scroller = event.currentTarget.closest<HTMLElement>(
      "[data-project-scroll]",
    );
    if (scroller) {
      const bounds = scroller.getBoundingClientRect();
      if (event.clientY < bounds.top + 40) scroller.scrollTop -= 18;
      if (event.clientY > bounds.bottom - 40) scroller.scrollTop += 18;
    }
    const target = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest<HTMLElement>("[data-project-key]");
    const targetId = target?.dataset.projectKey;
    if (!target || !targetId || targetId === drag.id || !ids.includes(targetId))
      return;
    const bounds = target.getBoundingClientRect();
    const without = current.filter((id) => id !== drag.id);
    setPreview(
      move(
        current,
        drag.id,
        without.indexOf(targetId) +
          (event.clientY > bounds.top + bounds.height / 2 ? 1 : 0),
      ),
    );
  };
  return {
    ids: current,
    moving,
    announcement,
    cancel,
    reset: () => {
      cancel();
      commit([]);
    },
    pointerEvents: {
      onPointerMove: pointerMove,
      onPointerUp: () => {
        if (pointer.current?.started) {
          commit(current);
          setAnnouncement("Project order saved in this browser.");
        }
        pointer.current = null;
      },
      onPointerCancel: cancel,
      onLostPointerCapture: () => {
        if (pointer.current) cancel();
      },
    },
    moveBy: (id: string, delta: number) =>
      commit(move(current, id, current.indexOf(id) + delta)),
    handle: (id: string) => ({
      ref: (element: HTMLElement | null) => {
        if (element) handles.current.set(id, element);
        else handles.current.delete(id);
      },
      onKeyDown: (event: KeyboardEvent) => keyboard(event, id),
      onPointerDown: (event: PointerEvent<HTMLElement>) => {
        if (event.button !== 0) return;
        // Capture on the stable scroll container, not the group that moves in the DOM.
        event.currentTarget
          .closest<HTMLElement>("[data-project-scroll]")
          ?.setPointerCapture(event.pointerId);
        pointer.current = {
          id,
          x: event.clientX,
          y: event.clientY,
          started: false,
        };
      },
    }),
  };
}
