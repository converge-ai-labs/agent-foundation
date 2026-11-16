import { useId, type ReactNode } from "react";
import styles from "./environments.module.css";

/** A semantic group inside the single template configuration panel. */
export function ConfigurationGroup({
  title,
  description,
  nested = false,
  children,
}: {
  title: string;
  description?: ReactNode;
  nested?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  const Heading = nested ? "h5" : "h4";
  return (
    <section className={styles.configurationGroup} aria-labelledby={id}>
      <div className={styles.configurationGroupHeading}>
        <Heading id={id}>{title}</Heading>
        {description && <p>{description}</p>}
      </div>
      {children}
    </section>
  );
}
