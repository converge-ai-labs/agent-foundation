import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import type { Schema } from "../../../../shared/api";
import type { ViewLevel } from "../../api";

type Thread = Pick<Schema["ThreadResource"], "session_purpose" | "role">;

/**
 * Where a Thread opens when the URL says nothing: an execution Session and a
 * child Thread are read at the Debug level, everything else as a conversation.
 * It is a default, not a lock; the reader can still ask for the other level.
 */
function defaultLevel(thread?: Thread | null): ViewLevel {
  return thread &&
    (thread.session_purpose === "execution" || thread.role === "child")
    ? "debug"
    : "chat";
}

/**
 * The disclosure level lives in the URL and nowhere else, so a deep link and a
 * reload select the same level the reader chose. Switching always writes the
 * level, so Chat on a Thread that opens in Debug survives a reload.
 */
export function useViewLevel(
  thread?: Thread | null,
  { chatOnly = false }: { chatOnly?: boolean } = {},
) {
  const [search, setSearch] = useSearchParams();
  const requested = search.get("view");
  const level: ViewLevel = chatOnly
    ? "chat"
    : requested === "debug" || requested === "chat"
      ? requested
      : defaultLevel(thread);
  const setLevel = useCallback(
    (next: ViewLevel) => {
      const params = new URLSearchParams(search);
      params.set("view", next);
      setSearch(params, { replace: true });
    },
    [search, setSearch],
  );
  return { level, setLevel };
}

/** Switching levels keeps the run the reader was looking at under their eyes. */
export function useAnchoredLevel(thread?: Thread | null) {
  const { level, setLevel } = useViewLevel(thread);
  const anchor = useRef<string | null>(null);
  useEffect(() => {
    const id = anchor.current;
    if (!id) return;
    let frame = 0;
    let attempts = 0;
    const settle = () => {
      const section = runSection(id);
      if (section) {
        section.scrollIntoView({ block: "start" });
        anchor.current = null;
        return;
      }
      // The other level mounts its own sections; wait for the one we followed.
      if (attempts++ < 60) frame = requestAnimationFrame(settle);
      else anchor.current = null;
    };
    frame = requestAnimationFrame(settle);
    return () => cancelAnimationFrame(frame);
  }, [level]);
  return {
    level,
    switchLevel(next: ViewLevel) {
      anchor.current = topmostRun();
      setLevel(next);
    },
  };
}

/** The mounted section of one Run, matched by identity rather than a selector. */
export function runSection(id: string): HTMLElement | null {
  for (const section of document.querySelectorAll<HTMLElement>("[data-run]"))
    if (section.dataset.run === id) return section;
  return null;
}

/** The run section the reader is reading: the first one still in the viewport. */
function topmostRun(): string | null {
  const stage = document.querySelector("[data-session-stage]");
  if (!(stage instanceof HTMLElement)) return null;
  const top = stage.getBoundingClientRect().top;
  for (const section of stage.querySelectorAll<HTMLElement>("[data-run]"))
    if (section.getBoundingClientRect().bottom > top + 1)
      return section.dataset.run ?? null;
  return null;
}
