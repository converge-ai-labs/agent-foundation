import type { ReactNode } from "react";
import styles from "./form-section.module.css";

export { default as formSectionStyles } from "./form-section.module.css";

export function FormSection({
  title,
  description,
  aside = false,
  children,
}: {
  title?: string;
  description?: string;
  aside?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={styles.section} data-aside={aside || undefined}>
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
