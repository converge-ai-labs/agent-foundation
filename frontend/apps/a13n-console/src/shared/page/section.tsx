import { createContext, useContext, useId, type ReactNode } from "react";
import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./page.module.css";

// Sections inside a group are its subsections.
const Grouped = createContext(false);

/**
 * Titled group, separated by whitespace; only its contents carry surfaces.
 * Inside a `SectionGroup` it is a subsection with a smaller heading.
 */
export function Section({
  id,
  title,
  titleLink,
  titleActions,
  description,
  actions,
  className,
  children,
}: {
  /** Anchor that validation can scroll to. */
  id?: string;
  title: ReactNode;
  titleLink?: { href: string; label: string };
  titleActions?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const headingId = useId();
  const grouped = useContext(Grouped);
  const Heading = grouped ? "h3" : "h2";
  return (
    <section
      id={id}
      className={`${styles.section} ${grouped ? styles.subsection : ""} ${className ?? ""}`}
      aria-labelledby={headingId}
    >
      <header className={styles.sectionHeader}>
        <div className="min-w-0">
          {titleLink || titleActions ? (
            <div className={styles.sectionTitle}>
              <Heading id={headingId}>{title}</Heading>
              <div className={styles.sectionTitleActions}>
                {titleLink && (
                  <a
                    href={titleLink.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={styles.sectionTitleLink}
                    aria-label={`${titleLink.label} · ${t("Opens in a new tab")}`}
                    title={`${titleLink.label} · ${t("Opens in a new tab")}`}
                  >
                    <ArrowSquareOutIcon size={14} aria-hidden="true" />
                  </a>
                )}
                {titleActions}
              </div>
            </div>
          ) : (
            <Heading id={headingId}>{title}</Heading>
          )}
          {description && <p>{description}</p>}
        </div>
        {actions && <div className={styles.sectionActions}>{actions}</div>}
      </header>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}

/** A section whose content is subsections, such as a form's chapter. */
export function SectionGroup({
  children,
  ...props
}: Omit<Parameters<typeof Section>[0], "className">) {
  return (
    <Section {...props} className={styles.sectionGroup}>
      <Grouped.Provider value>{children}</Grouped.Provider>
    </Section>
  );
}

/**
 * A long form's sections as rows: each title and summary stands in a column
 * beside its content and stays in view while the content scrolls; whitespace
 * alone divides the rows. A narrow container stacks each title above its
 * content.
 */
export function Sections({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`${styles.sections} ${className ?? ""}`}>{children}</div>
  );
}
