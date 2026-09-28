import { MenuItem } from "a13n-ui";
import { PencilSimpleIcon, PlugsIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { PageActions } from "../../shared/page";
import { ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import { providerCategory, type ProviderCategoryValue } from "./categories";
import { CredentialsPill, type CredentialState } from "./credentials-pill";

/**
 * What every provider category has in common. Category modules translate their
 * own resource into this shape; nothing here knows a specific backend.
 */
export interface ProviderRow {
  id: string;
  /** The provider's own name — never a raw type key. */
  name: string;
  /** Definition display name, shown as the secondary line. */
  definition?: string | null;
  /** Brand identity for the tile. */
  type: string;
  credentials: CredentialState;
  /** Domain state for the status pill (`enabled`, `disabled`, …). */
  state: string;
  stateLabel?: string;
}

/**
 * One table anatomy for models, web, environment, and connector
 * providers: brand identity, credentials, status, overflow menu.
 */
export function ProviderTable<T extends { id: string }>({
  category,
  items,
  row,
  isPending,
  error,
  page,
  nextCursor,
  onRowActivate,
  canActivateRow,
  rowMenu,
  action,
  notice,
  emptyAction,
}: {
  category: ProviderCategoryValue;
  items?: readonly T[];
  row: (item: T) => ProviderRow;
  isPending?: boolean;
  error?: unknown;
  page?: ReturnType<typeof useCursor>;
  nextCursor?: string | null;
  onRowActivate?: (item: T, element: HTMLElement) => void;
  canActivateRow?: (item: T) => boolean;
  /** Extra overflow entries after Edit; each is a `MenuItem`. */
  rowMenu?: (item: T) => ReactNode;
  /** Rendered above the table, before the collection. */
  notice?: ReactNode;
  /**
   * The creation action. It sits in the page header, and moves into the empty
   * state when there is nothing to list, so a page never offers it twice.
   */
  action?: ReactNode;
  emptyAction?: ReactNode;
}) {
  const { t } = useTranslation();
  const entry = providerCategory(category);
  const opens = (item: T) =>
    !!onRowActivate && (canActivateRow?.(item) ?? true);
  if (error && !items?.length)
    return (
      <div className={styles.stack}>
        <PageActions>{action}</PageActions>
        {notice}
        <ErrorNotice error={error} />
      </div>
    );
  if (isPending)
    return (
      <div className={styles.stack}>
        <PageActions>{action}</PageActions>
        {notice}
        <Loading variant="table" columns={4} />
      </div>
    );
  if (!items?.length)
    return (
      <div className={styles.stack}>
        {notice}
        <Empty
          icon={<PlugsIcon aria-hidden="true" />}
          title={t(entry.emptyTitle)}
          description={t(entry.empty)}
          action={emptyAction ?? action}
        />
      </div>
    );
  return (
    <div className={styles.stack}>
      <PageActions>{action}</PageActions>
      {notice}
      <ResourceTable
        items={items}
        caption={t("Providers")}
        onRowActivate={onRowActivate}
        canActivateRow={canActivateRow}
        rowMenu={
          onRowActivate || rowMenu
            ? (item) => (
                <ProviderRowMenu
                  edit={
                    opens(item)
                      ? (element) => onRowActivate?.(item, element)
                      : undefined
                  }
                >
                  {rowMenu?.(item)}
                </ProviderRowMenu>
              )
            : undefined
        }
        columns={[
          {
            label: t("Provider"),
            tone: "primary",
            render: (item) => {
              const value = row(item);
              return (
                <ResourceIdentity
                  icon={<ProviderIcon type={value.type} />}
                  name={value.name}
                  description={value.definition ?? undefined}
                  resourceId={value.id}
                />
              );
            },
          },
          {
            label: t("Credentials"),
            render: (item) => <CredentialsPill state={row(item).credentials} />,
          },
          {
            label: t("Status"),
            render: (item) => {
              const value = row(item);
              return <StatePill state={value.state} label={value.stateLabel} />;
            },
          },
        ]}
      />
      <CollectionFooter
        count={t("{{count}} providers", { count: items.length })}
      >
        {page && <Pagination page={page} next={nextCursor} />}
      </CollectionFooter>
    </div>
  );
}

function ProviderRowMenu({
  edit,
  children,
}: {
  edit?: (element: HTMLElement) => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!edit && !children) return null;
  return (
    <>
      {edit && (
        <MenuItem onClick={(event) => edit(event.currentTarget as HTMLElement)}>
          <PencilSimpleIcon aria-hidden="true" />
          {t("Edit")}
        </MenuItem>
      )}
      {children}
    </>
  );
}
