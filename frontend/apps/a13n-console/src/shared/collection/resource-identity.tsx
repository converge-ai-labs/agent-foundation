import type { ReactNode } from "react";
import { Link } from "react-router";
import styles from "../shared.module.css";
export function ResourceIdentity({
  name,
  description,
  icon,
  to,
}: {
  name: string;
  description?: ReactNode;
  icon: ReactNode;
  to?: string;
}) {
  const content = (
    <>
      <span className={styles.resourceIcon}>{icon}</span>
      <span className={styles.resourceCopy}>
        <strong>{name}</strong>
        {description && <small>{description}</small>}
      </span>
    </>
  );
  return to ? (
    <Link to={to} className={styles.resourceIdentity}>
      {content}
    </Link>
  ) : (
    <div className={styles.resourceIdentity}>{content}</div>
  );
}
