import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
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
  const current = orderedProjects(ids, preview ?? saved);
  const commit = (next: string[]) => {
    setSaved(next);
    savePreference(ORDER_KEY, next);
    setPreview(null);
    setMoving(null);
  };
  const cancel = useCallback(() => {
    setPreview(null);
    setMoving(null);
  }, []);
  const handles = useRef(new Map<string, HTMLElement>());
  useLayoutEffect(() => {
    // Moving a keyed DOM node can drop keyboard focus. Restore the active handle.
    if (moving) handles.current.get(moving)?.focus();
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
    if (event.key === "Escape" && moving === id) {
      event.preventDefault();
      cancel();
      setAnnouncement("Reordering cancelled.");
      return;
    }
    if (event.key === " " || event.key === "Enter") {
      if (moving !== id && !(event.altKey && event.key === " ")) return;
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
  return {
    ids: current,
    moving,
    announcement,
    cancel,
    reset: () => {
      cancel();
      commit([]);
    },
    place: (id: string, target: string, after: boolean) =>
      commit(placeItem(current, id, target, after)),
    moveBy: (id: string, delta: number) =>
      commit(move(current, id, current.indexOf(id) + delta)),
    handle: (id: string) => ({
      ref: (element: HTMLElement | null) => {
        if (element) handles.current.set(id, element);
        else handles.current.delete(id);
      },
      onKeyDown: (event: KeyboardEvent) => keyboard(event, id),
    }),
  };
}

export function placeItem(
  ids: string[],
  id: string,
  target: string,
  after: boolean,
) {
  if (id === target || !ids.includes(target)) return ids;
  const next = ids.filter((item) => item !== id);
  next.splice(next.indexOf(target) + Number(after), 0, id);
  return next;
}

const CHAT_ORDER_KEY = "a13n-harness-ui.chat-order";
export function useChatOrder() {
  const [saved, setSaved] = useState<Record<string, string[]>>(() => {
    const value = readPreference(CHAT_ORDER_KEY);
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, ids]) => Array.isArray(ids))
        .map(([key, ids]) => [
          key,
          (ids as unknown[]).filter(
            (id): id is string => typeof id === "string",
          ),
        ]),
    );
  });
  return {
    ids: (group: string, ids: string[]) =>
      orderedProjects(ids, saved[group] ?? []),
    place: (
      group: string,
      ids: string[],
      id: string,
      target: string,
      after: boolean,
    ) => {
      setSaved((current) => {
        // Keep unloaded/temporarily active rows in the preference, so pagination
        // and activity transitions cannot silently discard an earlier arrangement.
        const next = {
          ...current,
          [group]: placeItem(
            [...new Set([...(current[group] ?? []), ...ids])],
            id,
            target,
            after,
          ),
        };
        savePreference(CHAT_ORDER_KEY, next);
        return next;
      });
    },
    reset: (group: string) =>
      setSaved((current) => {
        const next = { ...current };
        delete next[group];
        savePreference(CHAT_ORDER_KEY, next);
        return next;
      }),
  };
}
