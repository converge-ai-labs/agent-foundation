import type { ReactNode } from "react";
import styles from "./agents.module.css";

export function EditorSection({
  title,
  description,
  children,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.editorSection}>
      <header>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      <div className={styles.sectionContent}>{children}</div>
    </section>
  );
}
