import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";
import styles from "./shared.module.css";

export const PageActionsTarget = createContext<HTMLDivElement | null>(null);

// Resource editors retain their state while presenting actions in the page header.
export function PageActions({ children }: { children: ReactNode }) {
  const target = useContext(PageActionsTarget);
  return target ? (
    createPortal(children, target)
  ) : (
    <div className={styles.actions}>{children}</div>
  );
}
