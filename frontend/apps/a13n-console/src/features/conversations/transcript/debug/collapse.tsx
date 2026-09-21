import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/**
 * Whether each Run section is open has one owner, above the list of Runs, so
 * the header can collapse or expand them all and a section can still be opened
 * on its own. A Run loaded afterwards follows the choice already made.
 */
interface RunCollapse {
  collapsed(runId: string): boolean;
  toggle(runId: string): void;
  /** The reader last asked for every Run to be collapsed. */
  allCollapsed: boolean;
  setAll(collapsed: boolean): void;
}

const Context = createContext<RunCollapse | null>(null);

export function RunCollapseProvider({ children }: { children: ReactNode }) {
  const [allCollapsed, setAllCollapsed] = useState(false);
  const [exceptions, setExceptions] = useState<ReadonlySet<string>>(new Set());
  const value = useMemo<RunCollapse>(
    () => ({
      allCollapsed,
      collapsed: (runId) =>
        exceptions.has(runId) ? !allCollapsed : allCollapsed,
      toggle: (runId) =>
        setExceptions((current) => {
          const next = new Set(current);
          if (!next.delete(runId)) next.add(runId);
          return next;
        }),
      setAll: (collapsed) => {
        setAllCollapsed(collapsed);
        setExceptions(new Set());
      },
    }),
    [allCollapsed, exceptions],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** The section's own open state, from the shared owner when there is one. */
export function useRunOpen(runId: string): [boolean, () => void] {
  const shared = useContext(Context);
  const [own, setOwn] = useState(true);
  if (!shared) return [own, () => setOwn((value) => !value)];
  return [!shared.collapsed(runId), () => shared.toggle(runId)];
}

/** The header's control over every loaded section, when it is on the page. */
export function useRunCollapseAll() {
  return useContext(Context);
}
