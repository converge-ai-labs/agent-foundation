import { Skeleton, Spinner } from "a13n-ui";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import styles from "./feedback.module.css";

type LoadingVariant =
  "status" | "table" | "cards" | "form" | "detail" | "list" | "code" | "page";

export function Loading({
  page = false,
  variant = "status",
  columns = 4,
  rows,
}: {
  page?: boolean;
  variant?: LoadingVariant;
  columns?: number;
  rows?: number;
}) {
  const { t } = useTranslation();
  if (variant !== "status")
    return (
      <div
        role="status"
        aria-busy="true"
        className={`${styles.skeletonLoading} ${page ? styles.pageSkeleton : ""}`}
        data-variant={variant}
      >
        <span className="sr-only">{t("Loading…")}</span>
        <div aria-hidden="true">
          {variant === "table" ? (
            <TableSkeleton columns={columns} rows={rows ?? 6} />
          ) : variant === "cards" ? (
            <CardsSkeleton rows={rows ?? 6} />
          ) : variant === "form" ? (
            <FormSkeleton rows={rows ?? 4} />
          ) : variant === "detail" ? (
            <DetailSkeleton />
          ) : variant === "code" ? (
            <CodeSkeleton rows={rows ?? 8} />
          ) : variant === "page" ? (
            <PageSkeleton columns={columns} rows={rows ?? 6} />
          ) : (
            <ListSkeleton rows={rows ?? 4} />
          )}
        </div>
      </div>
    );
  return (
    <div
      role="status"
      className={`${styles.loading} ${page ? styles.pageLoading : ""}`}
    >
      <Spinner aria-hidden="true" />
      {t("Loading…")}
    </div>
  );
}

export function InlineLoading({ width = "6rem" }: { width?: string }) {
  const { t } = useTranslation();
  return (
    <span className={styles.inlineSkeleton} role="status" aria-busy="true">
      <span className="sr-only">{t("Loading…")}</span>
      <Skeleton aria-hidden="true" style={{ width }} />
    </span>
  );
}

function TableSkeleton({ columns, rows }: { columns: number; rows: number }) {
  const safeColumns = Math.max(1, columns);
  const template =
    safeColumns === 1
      ? "minmax(12rem, 1fr)"
      : `minmax(12rem, 2fr) repeat(${safeColumns - 1}, minmax(6rem, 1fr))`;
  const style = { gridTemplateColumns: template } as CSSProperties;
  return (
    <div className={styles.skeletonTable}>
      <div className={styles.skeletonTableRow} data-header style={style}>
        {Array.from({ length: safeColumns }, (_, column) => (
          <Skeleton key={column} className={styles.skeletonTableHeading} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div className={styles.skeletonTableRow} key={row} style={style}>
          {Array.from({ length: safeColumns }, (_, column) => (
            <div className={styles.skeletonTableCell} key={column}>
              {column === 0 && <Skeleton className={styles.skeletonIcon} />}
              <div className={styles.skeletonCellCopy}>
                <Skeleton
                  className={styles.skeletonLine}
                  data-width={(row + column) % 3}
                />
                {column === 0 && (
                  <Skeleton
                    className={styles.skeletonLine}
                    data-secondary
                    data-width={(row + 1) % 3}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CardsSkeleton({ rows }: { rows: number }) {
  return (
    <div className={styles.skeletonCards}>
      {Array.from({ length: rows }, (_, row) => (
        <div className={styles.skeletonCard} key={row}>
          <div className={styles.skeletonCardHeading}>
            <Skeleton className={styles.skeletonTitle} />
            <Skeleton className={styles.skeletonBadge} />
          </div>
          <Skeleton className={styles.skeletonKey} />
          <div className={styles.skeletonCardFooter}>
            <Skeleton className={styles.skeletonMeta} />
            <Skeleton className={styles.skeletonDate} />
          </div>
        </div>
      ))}
    </div>
  );
}

function FormSkeleton({ rows }: { rows: number }) {
  return (
    <div className={styles.skeletonForm}>
      <Skeleton className={styles.skeletonFormIntro} />
      {Array.from({ length: rows }, (_, row) => (
        <div className={styles.skeletonField} key={row}>
          <Skeleton className={styles.skeletonLabel} />
          <Skeleton className={styles.skeletonControl} />
        </div>
      ))}
      <div className={styles.skeletonActions}>
        <Skeleton />
        <Skeleton />
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className={styles.skeletonDetail}>
      <header>
        <div>
          <Skeleton className={styles.skeletonDetailTitle} />
          <Skeleton className={styles.skeletonDetailDescription} />
        </div>
        <Skeleton className={styles.skeletonDetailAction} />
      </header>
      <Skeleton className={styles.skeletonTabs} />
      <div className={styles.skeletonDetailBody}>
        <Skeleton className={styles.skeletonSectionTitle} />
        <Skeleton className={styles.skeletonParagraph} />
        <Skeleton className={styles.skeletonPanel} />
        <Skeleton className={styles.skeletonSectionTitle} />
        <Skeleton className={styles.skeletonPanel} />
      </div>
    </div>
  );
}

/** Route transitions to a known list page keep the destination's layout. */
function PageSkeleton({ columns, rows }: { columns: number; rows: number }) {
  return (
    <div className={styles.skeletonPage}>
      <div className={styles.skeletonPageHeader}>
        <div>
          <Skeleton className={styles.skeletonDetailTitle} />
          <Skeleton className={styles.skeletonDetailDescription} />
        </div>
        <Skeleton className={styles.skeletonDetailAction} />
      </div>
      <Skeleton className={styles.skeletonToolbar} />
      <TableSkeleton columns={columns} rows={rows} />
    </div>
  );
}

function ListSkeleton({ rows }: { rows: number }) {
  return (
    <div className={styles.skeletonList}>
      {Array.from({ length: rows }, (_, row) => (
        <div className={styles.skeletonListRow} key={row}>
          <Skeleton className={styles.skeletonIcon} />
          <div>
            <Skeleton className={styles.skeletonLine} data-width={row % 3} />
            <Skeleton
              className={styles.skeletonLine}
              data-secondary
              data-width={(row + 1) % 3}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function CodeSkeleton({ rows }: { rows: number }) {
  return (
    <div className={styles.skeletonCode}>
      {Array.from({ length: rows }, (_, row) => (
        <Skeleton
          className={styles.skeletonLine}
          data-width={row % 3}
          key={row}
        />
      ))}
    </div>
  );
}
