import type { ReactNode } from "react";
import type { PresentedItem } from "../projection";
import { ExecutionRow } from "./execution-row";
import styles from "./transcript.module.css";

/** Contiguous execution steps share one surface so prose stays the foreground. */
export function ExecutionGroup({
  items,
  runState,
  children,
}: {
  items: readonly PresentedItem[];
  runState?: string;
  children?: ReactNode;
}) {
  return (
    <div className={styles.executionGroup}>
      {items.map((item) => (
        <ExecutionRow key={item.id} item={item} runState={runState} />
      ))}
      {children}
    </div>
  );
}
