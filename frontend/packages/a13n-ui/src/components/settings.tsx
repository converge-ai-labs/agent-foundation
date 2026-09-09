import type { ReactNode } from "react";
import { useId } from "react";
import styles from "./settings.module.css";
export function SettingsSection({
  title,
  description,
  children,
  variant = "grouped",
}: {
  variant?: "grouped" | "plain";
  title?: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      className={styles.section}
      data-variant={variant}
      aria-labelledby={title ? id : undefined}
    >
      {title && (
        <div className={styles.heading}>
          <h3 id={id}>{title}</h3>
          {description && <p>{description}</p>}
        </div>
      )}
      <div className={styles.group}>{children}</div>
    </section>
  );
}
export function SettingsRow({
  label,
  description,
  controlId,
  children,
}: {
  label: string;
  description?: ReactNode;
  controlId?: string;
  children: ReactNode;
}) {
  return (
    <div className={styles.row}>
      <div className={styles.copy}>
        {controlId ? (
          <label htmlFor={controlId}>{label}</label>
        ) : (
          <span>{label}</span>
        )}
        {description && (
          <p id={controlId ? `${controlId}-description` : undefined}>
            {description}
          </p>
        )}
      </div>
      <div className={styles.control}>{children}</div>
    </div>
  );
}
