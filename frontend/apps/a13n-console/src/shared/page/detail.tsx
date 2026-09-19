import { Tabs, TabsList, TabsTab } from "a13n-ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { ResourceKeyChip } from "../identity";
import styles from "./page.module.css";

export interface DetailTab {
  value: string;
  label: ReactNode;
  count?: ReactNode;
}

/**
 * Reads and writes the active tab through `?tab=`. The first tab is the
 * default and is never written to the URL.
 */
export function useTabParam(
  tabs: readonly string[],
): [string, (value: string) => void] {
  const [search, setSearch] = useSearchParams();
  const requested = search.get("tab");
  const value = requested && tabs.includes(requested) ? requested : tabs[0];
  return [
    value,
    (next: string) =>
      setSearch(
        (current) => {
          const params = new URLSearchParams(current);
          if (next === tabs[0]) params.delete("tab");
          else params.set("tab", next);
          return params;
        },
        { replace: true },
      ),
  ];
}

/**
 * Detail page anatomy: back link, identity header, underline tabs, then the
 * content column with an optional sticky rail.
 */
export function DetailPage({
  back,
  backLabel,
  header,
  tabs,
  tab,
  onTabChange,
  rail,
  className,
  children,
}: {
  back?: string;
  backLabel?: string;
  header: ReactNode;
  tabs?: readonly DetailTab[];
  tab?: string;
  onTabChange?: (value: string) => void;
  rail?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={`${styles.detailPage} ${className ?? ""}`}>
      {back && (
        <Link className={styles.back} to={back}>
          <ArrowLeftIcon size={13} />
          {backLabel ?? t("Back")}
        </Link>
      )}
      {header}
      {tabs && tabs.length > 1 && (
        <Tabs value={tab} onValueChange={onTabChange} className={styles.tabs}>
          <TabsList variant="underline">
            {tabs.map((item) => (
              <TabsTab key={item.value} value={item.value}>
                {item.label}
                {item.count !== undefined && (
                  <span data-slot="tab-count">{item.count}</span>
                )}
              </TabsTab>
            ))}
          </TabsList>
        </Tabs>
      )}
      <div className={styles.content}>
        {rail ? <DetailLayout rail={rail}>{children}</DetailLayout> : children}
      </div>
    </div>
  );
}

/**
 * Content column plus an optional 256px sticky rail. Editors that own a form
 * element use this directly so the rail and save bar stay inside the form.
 */
export function DetailLayout({
  rail,
  className,
  children,
}: {
  rail?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`${styles.layout} ${className ?? ""}`}
      data-rail={rail ? "true" : "false"}
    >
      <div className={styles.main}>{children}</div>
      {rail && <aside className={styles.rail}>{rail}</aside>}
    </div>
  );
}

/** Identity header: brand tile, name, status, edit affordance, key, summary. */
export function DetailHeader({
  avatar,
  name,
  status,
  edit,
  resourceKey,
  description,
  actions,
}: {
  avatar?: ReactNode;
  name: string;
  status?: ReactNode;
  edit?: ReactNode;
  resourceKey?: string;
  description?: string | null;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        {avatar}
        <div className={styles.titleBlock}>
          <div className={styles.detailTitleRow}>
            <h1>{name}</h1>
            {status}
            {edit}
          </div>
          {(resourceKey || description) && (
            <div className={styles.subtitle}>
              {resourceKey && <ResourceKeyChip value={resourceKey} />}
              {resourceKey && description && (
                <span className={styles.dot}>·</span>
              )}
              {description && (
                <span className={styles.subtitleText} title={description}>
                  {description}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}

/** Rail: a stack of quiet summary groups beside the detail content. */
export function Rail({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function RailSection({
  title,
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <dl className={styles.railSection}>
      {title && <h2>{title}</h2>}
      {children}
    </dl>
  );
}

export function RailRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className={styles.railRow}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function RailNote({ children }: { children: ReactNode }) {
  return <p className={styles.railNote}>{children}</p>;
}
