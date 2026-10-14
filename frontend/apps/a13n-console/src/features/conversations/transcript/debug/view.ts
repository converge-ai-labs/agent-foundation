import { useCallback } from "react";
import { useSearchParams } from "react-router";
import type { Schema } from "../../../../shared/api";
import { isConsoleSession, type ViewLevel } from "../../api";

type Thread = Pick<Schema["ThreadView"], "origin">;
type Session = Pick<Schema["SessionView"], "labels">;

/**
 * Where a Thread opens when the URL says nothing: a Session an application
 * started and a child Thread are read at the Debug level, everything else as
 * a conversation. It is a default, not a lock; the reader can still ask for
 * the other level.
 */
function defaultLevel(
  thread?: Thread | null,
  session?: Session | null,
): ViewLevel {
  return thread?.origin === "child" || (!!session && !isConsoleSession(session))
    ? "debug"
    : "chat";
}

/**
 * The disclosure level lives in the URL and nowhere else, so a deep link and a
 * reload select the same level the reader chose. Switching always writes the
 * level, so Chat on a Thread that opens in Debug survives a reload.
 */
export function useViewLevel(thread?: Thread | null, session?: Session | null) {
  const [search, setSearch] = useSearchParams();
  const requested = search.get("view");
  const level: ViewLevel =
    requested === "debug" || requested === "chat"
      ? requested
      : defaultLevel(thread, session);
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

/** The mounted section of one Run, matched by identity rather than a selector. */
export function runSection(id: string): HTMLElement | null {
  for (const section of document.querySelectorAll<HTMLElement>("[data-run]"))
    if (section.dataset.run === id) return section;
  return null;
}
