import type { ReactNode } from "react";
import { IconTile, ResourceKeyChip, ResourceReference } from "../identity";
import styles from "./dialogs.module.css";

export function ResourceModalTitle({
  name,
  id,
  resourceKey,
  icon,
}: {
  name: string;
  /** Omitted for resources identified by key alone. */
  id?: string;
  resourceKey?: string;
  /** A brand or built-in mark, framed the way a creation step frames it. */
  icon?: ReactNode;
}) {
  const title = (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 truncate" title={name}>
        {name}
      </span>
      {resourceKey && <ResourceKeyChip value={resourceKey} />}
      {id && <ResourceReference id={id} />}
    </span>
  );
  return icon ? (
    <span className={styles.brandTitle}>
      <IconTile size={36}>{icon}</IconTile>
      {title}
    </span>
  ) : (
    title
  );
}
