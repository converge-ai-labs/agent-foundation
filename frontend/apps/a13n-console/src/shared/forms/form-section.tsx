import { useId, type ReactNode } from "react";
import styles from "./form-section.module.css";

export { default as formSectionStyles } from "./form-section.module.css";

export function FormSection({
  title,
  description,
  actions,
  children,
}: {
  title?: string;
  description?: ReactNode;
  /** Controls that belong to the group, aligned with its heading. */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section
      data-a13n-form-section
      className={styles.section}
      aria-labelledby={title ? headingId : undefined}
    >
      {(title || actions) && (
        <div className={styles.heading}>
          <div className="min-w-0">
            {title && <h3 id={headingId}>{title}</h3>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className={styles.headingActions}>{actions}</div>}
        </div>
      )}
      <div className={`${styles.fields} ${styles.fieldRows}`}>{children}</div>
    </section>
  );
}
