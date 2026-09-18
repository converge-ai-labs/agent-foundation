import type { ReactNode } from "react";
import styles from "./form-section.module.css";

export { default as formSectionStyles } from "./form-section.module.css";

export function FormSection({
  title,
  description,
  actions,
  divider = true,
  children,
}: {
  title?: string;
  description?: ReactNode;
  /** Controls that belong to the group, aligned with its heading. */
  actions?: ReactNode;
  /** Groups separated by whitespace alone omit the hairline above them. */
  divider?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={styles.section}
      data-divider={divider ? undefined : "none"}
    >
      {(title || actions) && (
        <div className={styles.heading}>
          <div className="min-w-0">
            {title && <h3>{title}</h3>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className={styles.headingActions}>{actions}</div>}
        </div>
      )}
      <div className={styles.fields}>{children}</div>
    </section>
  );
}
