import type { ReactNode } from "react";
import { Link } from "react-router";
import { ResourceReference } from "../resource-reference";
import styles from "../shared.module.css";
export function ResourceIdentity({
  name,
  description,
  icon,
  to,
  resourceId,
  resourceKey,
}: {
  name: string;
  description?: ReactNode;
  icon?: ReactNode;
  to?: string;
  resourceId?: string;
  resourceKey?: string;
}) {
  const content = (
    <>
      {icon && <span className={styles.resourceIcon}>{icon}</span>}
      <span className={styles.resourceCopy}>
        <strong title={name}>{name}</strong>
        {description && <small>{description}</small>}
      </span>
    </>
  );
  return (
    <div className={styles.resourceIdentity}>
      {to ? (
        <Link to={to} className={styles.resourceIdentityLink}>
          {content}
        </Link>
      ) : (
        <div className={styles.resourceIdentityLink}>{content}</div>
      )}
      {resourceId && (
        <ResourceReference id={resourceId} resourceKey={resourceKey} />
      )}
    </div>
  );
}
