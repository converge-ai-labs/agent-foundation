import type { ReactNode } from "react";
import styles from "./menu.module.css";
/** Presentation shared across selection lists and action menus; semantics stay with their owners. */
export function OptionContent({
  icon,
  label,
  description,
  trailing,
}: {
  icon?: ReactNode;
  label: string;
  description?: string;
  trailing?: ReactNode;
}) {
  return (
    <>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      <span className={styles.copy}>
        <span>{label}</span>
        {description && (
          <span className={styles.description}>{description}</span>
        )}
      </span>
      {trailing && (
        <span className={styles.trailing} aria-hidden="true">
          {trailing}
        </span>
      )}
    </>
  );
}
