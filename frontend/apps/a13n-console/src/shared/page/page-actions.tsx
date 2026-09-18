import { createContext, useContext, useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import styles from "./page.module.css";

export const PageActionsTarget = createContext<HTMLDivElement | null>(null);

/** Set by `Page` so an empty state can claim the page's primary action. */
export const PageEmptyAction = createContext<
  ((offered: boolean) => void) | null
>(null);

/**
 * An empty state that already offers the primary action tells the page, so the
 * header does not repeat the same button beside the title.
 */
export function useOffersPageAction(offered: boolean) {
  const notify = useContext(PageEmptyAction);
  useEffect(() => {
    if (!notify || !offered) return;
    notify(true);
    return () => notify(false);
  }, [notify, offered]);
}

// Resource editors retain their state while presenting actions in the page header.
export function PageActions({ children }: { children: ReactNode }) {
  const target = useContext(PageActionsTarget);
  return target ? (
    createPortal(children, target)
  ) : (
    <div className={styles.actions}>{children}</div>
  );
}
