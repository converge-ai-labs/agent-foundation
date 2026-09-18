import { ArrowLeftIcon, CaretRightIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { ReactNode } from "react";
import { IconTile } from "../identity";
import styles from "./dialogs.module.css";

/**
 * Creation flows start with a catalog: a tile grid for a small fixed set, or a
 * searchable directory when entries are many and grouped.
 */
export function CatalogTiles({
  search,
  note,
  empty,
  children,
}: {
  search?: ReactNode;
  note?: ReactNode;
  empty?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.catalog}>
      {search}
      {children ? (
        <div className={styles.tiles}>{children}</div>
      ) : (
        empty && <p className={styles.catalogEmpty}>{empty}</p>
      )}
      {note && <p className={styles.catalogNote}>{note}</p>}
    </div>
  );
}

export function CatalogTile({
  icon,
  name,
  detail,
  onClick,
}: {
  icon: ReactNode;
  name: string;
  detail?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" className={styles.tile} onClick={onClick}>
      <IconTile size={36} tone="elevated">
        {icon}
      </IconTile>
      <span className={styles.tileCopy}>
        <strong>{name}</strong>
        {detail && <small>{detail}</small>}
      </span>
    </button>
  );
}

export function DirectoryList({
  search,
  footer,
  children,
}: {
  search?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={styles.directory}>
      {search}
      <div className={`a13n-scrollbar ${styles.directoryList}`}>{children}</div>
      {footer && <footer className={styles.catalogFooter}>{footer}</footer>}
    </div>
  );
}

export function DirectoryGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.group}>
      <h4>{label}</h4>
      {children}
    </section>
  );
}

export function DirectoryRow({
  icon,
  name,
  detail,
  meta,
  tone = "surface",
  onClick,
}: {
  icon: ReactNode;
  name: string;
  detail?: string | null;
  meta?: ReactNode;
  tone?: "surface" | "elevated";
  onClick: () => void;
}) {
  return (
    <button type="button" className={styles.directoryRow} onClick={onClick}>
      <IconTile size={36} tone={tone}>
        {icon}
      </IconTile>
      <span className={styles.directoryCopy}>
        <strong>{name}</strong>
        {detail && <small>{detail}</small>}
      </span>
      {meta ? (
        <span className={styles.directoryMeta}>{meta}</span>
      ) : (
        <CaretRightIcon
          aria-hidden="true"
          size={14}
          className="text-muted-foreground"
        />
      )}
    </button>
  );
}

export function DirectoryEmpty({ children }: { children: ReactNode }) {
  return <p className={styles.catalogEmpty}>{children}</p>;
}

/** Second step of a catalog-first flow: a way back plus the chosen form. */
export function CatalogStep({
  backLabel,
  onBack,
  children,
}: {
  backLabel: string;
  onBack?: () => void;
  children: ReactNode;
}) {
  return (
    <div className={styles.step}>
      {onBack && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={styles.stepBack}
          onClick={onBack}
        >
          <ArrowLeftIcon aria-hidden="true" />
          {backLabel}
        </Button>
      )}
      {children}
    </div>
  );
}
