import type { ReactNode } from "react";
import styles from "./empty-state.module.css";
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
