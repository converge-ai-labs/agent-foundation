import type { ReactNode } from "react";
import styles from "./form-section.module.css";

export { default as formSectionStyles } from "./form-section.module.css";

export function FormSection({
  title,
  description,
  children,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.section}>
      {title && (
        <div className={styles.heading}>
          <h3>{title}</h3>
          {description && <p>{description}</p>}
        </div>
      )}
      <div className={styles.fields}>{children}</div>
    </section>
  );
}
