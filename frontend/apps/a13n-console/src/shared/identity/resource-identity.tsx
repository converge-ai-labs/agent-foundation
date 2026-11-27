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
  badge,
  icon,
  to,
  resourceId,
  resourceKey,
}: {
  name: string;
  description?: ReactNode;
  /** A quiet static badge beside the supporting text. */
  badge?: ReactNode;
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
        {(badge || description) && (
          <span className={styles.resourceMeta}>
            {badge}
            {description && (
              <small
                title={
                  typeof description === "string" ? description : undefined
                }
              >
                {description}
              </small>
            )}
          </span>
        )}
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
      {(resourceId || resourceKey) && (
        <ResourceReference id={resourceId} resourceKey={resourceKey} />
      )}
    </div>
  );
}
