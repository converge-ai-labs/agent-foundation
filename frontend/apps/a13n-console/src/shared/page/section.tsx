import { useId, type ReactNode } from "react";
import styles from "./page.module.css";

/** Titled group separated by whitespace; only its contents carry surfaces. */
export function Section({
  title,
  description,
  actions,
  className,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section
      className={`${styles.section} ${className ?? ""}`}
      aria-labelledby={headingId}
    >
      <header className={styles.sectionHeader}>
        <div className="min-w-0">
          <h2 id={headingId}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {actions}
      </header>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}
