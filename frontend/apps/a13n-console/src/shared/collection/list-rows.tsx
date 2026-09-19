import type { ReactNode } from "react";
import { IconTile } from "../identity";
import styles from "./collection.module.css";

/** Surface rows: the list anatomy used inside sections and editors. */
export function ListRows({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={`${styles.rows} ${className ?? ""}`}>{children}</div>;
}

export function ListRow({
  icon,
  name,
  secondary,
  control,
  actions,
  className,
  children,
}: {
  icon?: ReactNode;
  name: ReactNode;
  secondary?: ReactNode;
  control?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`${styles.row} ${className ?? ""}`}>
      {icon && (
        <IconTile size={32} tone="elevated">
          {icon}
        </IconTile>
      )}
      <span className={styles.rowCopy}>
        <strong>{name}</strong>
        {secondary && <small>{secondary}</small>}
      </span>
      {children}
      {control && <span className={styles.rowControl}>{control}</span>}
      {actions && <span className={styles.rowActions}>{actions}</span>}
    </div>
  );
}

/** Quiet inline message shown in place of an empty row list. */
export function ListRowsEmpty({ children }: { children: ReactNode }) {
  return <p className={styles.rowsEmpty}>{children}</p>;
}
