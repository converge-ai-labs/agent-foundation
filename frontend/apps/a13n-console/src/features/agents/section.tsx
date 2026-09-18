import { createContext, useContext, type ReactNode } from "react";
import styles from "./agents.module.css";

/** Layout options shared by editor sections rendered inside one agent editor. */
export const EditorLayoutContext = createContext<{
  card: boolean;
  permissionLabels: boolean;
}>({ card: false, permissionLabels: false });

export function useEditorLayout() {
  return useContext(EditorLayoutContext);
}

export function EditorSection({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { card } = useEditorLayout();
  if (card)
    return (
      <section className={styles.cardSection}>
        <header className={styles.cardSectionHeader}>
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          {actions}
        </header>
        <div className={styles.cardSectionBody}>{children}</div>
      </section>
    );
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
