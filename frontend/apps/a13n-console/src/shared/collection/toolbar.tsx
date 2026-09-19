import { Input } from "a13n-ui";
import type { ReactNode } from "react";
import styles from "./collection.module.css";

/** Search at 300px, then filters, then secondary actions at the right. */
export function Toolbar({
  search,
  onSearchChange,
  searchLabel,
  searchPlaceholder,
  filters,
  trailing,
  children,
}: {
  search?: string;
  onSearchChange?: (value: string) => void;
  searchLabel?: string;
  /** Hint inside the field when it says more than the accessible name. */
  searchPlaceholder?: string;
  filters?: ReactNode;
  trailing?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.toolbar}>
      {onSearchChange && (
        <Input
          type="search"
          size="sm"
          className={styles.toolbarSearch}
          aria-label={searchLabel}
          placeholder={searchPlaceholder ?? searchLabel}
          value={search ?? ""}
          onChange={(event) => onSearchChange(event.target.value)}
        />
      )}
      {filters && <div className={styles.toolbarFilters}>{filters}</div>}
      {children}
      {trailing && <div className={styles.toolbarTrailing}>{trailing}</div>}
    </div>
  );
}

/** Result count at the left, pagination at the right. */
export function CollectionFooter({
  count,
  children,
}: {
  count?: ReactNode;
  children?: ReactNode;
}) {
  if (!count && !children) return null;
  return (
    <div className={styles.footer}>
      <p>{count}</p>
      <div className={styles.pagination}>{children}</div>
    </div>
  );
}
