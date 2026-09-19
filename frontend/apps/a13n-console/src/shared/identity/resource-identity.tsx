import type { ReactNode } from "react";
import { Link } from "react-router";
import styles from "./identity.module.css";
import { IconTile } from "./icon-tile";
import { ResourceReference } from "./resource-reference";

/**
 * First column of every collection: a 32px tile, the name, and one line of
 * secondary text. Identifiers stay behind the reference popover.
 */
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
      {icon && <IconTile size={32}>{icon}</IconTile>}
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
