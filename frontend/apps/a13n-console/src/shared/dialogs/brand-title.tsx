import type { ReactNode } from "react";
import { IconTile } from "../identity";
import styles from "./dialogs.module.css";

/** Creation steps carry the chosen brand in the dialog title. */
export function BrandTitle({
  mark,
  children,
}: {
  mark: ReactNode;
  children: ReactNode;
}) {
  return (
    <span className={styles.brandTitle}>
      <IconTile size={36}>{mark}</IconTile>
      {children}
    </span>
  );
}
