import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import type { Schema } from "../../../../shared/api";
import type { ViewLevel } from "../../api";

type Thread = Pick<Schema["ThreadResource"], "session_purpose" | "role">;

/**
 * Chat cannot describe work nobody in this Console controls: an execution
 * Session and every child Thread are inspected in Debug, with no switch.
 */
function forcedDebug(thread?: Thread | null) {
  return (
    !!thread &&
    (thread.session_purpose === "execution" || thread.role === "child")
  );
}

/**
 * The disclosure level lives in the URL and nowhere else, so a deep link and a
 * reload select the same level the reader chose.
 */
export function useViewLevel(
  thread?: Thread | null,
  { chatOnly = false }: { chatOnly?: boolean } = {},
) {
  const [search, setSearch] = useSearchParams();
  const forced = !chatOnly && forcedDebug(thread);
  const level: ViewLevel = chatOnly
    ? "chat"
    : forced || search.get("view") === "debug"
      ? "debug"
      : "chat";
  const setLevel = useCallback(
    (next: ViewLevel) => {
      const params = new URLSearchParams(search);
      if (next === "debug") params.set("view", "debug");
      else params.delete("view");
      setSearch(params, { replace: true });
    },
    [search, setSearch],
  );
  return { level, forced, setLevel };
}

/** Switching levels keeps the run the reader was looking at under their eyes. */
export function useAnchoredLevel(thread?: Thread | null) {
  const { level, forced, setLevel } = useViewLevel(thread);
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
    forced,
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
