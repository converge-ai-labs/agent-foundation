import type { ReactNode } from "react";
import styles from "./environments.module.css";

/** One label and value row of an environment inspector group. */
export function Fact({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className={styles.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
