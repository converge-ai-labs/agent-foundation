import {
  Button,
  Menu,
  MenuPopup,
  MenuTrigger,
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "a13n-ui";
import { DotsThreeOutlineVerticalIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import styles from "./collection.module.css";

export interface ResourceColumn<T> {
  label: string;
  align?: "left" | "right";
  tone?: "primary" | "secondary" | "muted";
  header?: ReactNode;
  ariaSort?: "ascending" | "descending" | "none";
  dataColumn?: string;
  render: (item: T) => ReactNode;
}

/** One table anatomy serves every resource list; models are keyed rather than identified. */
export function ResourceTable<T extends { id: string } | { key: string }>({
  items,
  columns,
  caption,
  onRowActivate,
  canActivateRow,
  rowMenu,
  rowMenuLabel,
  className,
}: {
  items: readonly T[];
  columns: readonly ResourceColumn<T>[];
  caption?: string;
  onRowActivate?: (item: T, element: HTMLElement) => void;
  canActivateRow?: (item: T) => boolean;
  /** Overflow menu items for one row; the column is added when provided. */
  rowMenu?: (item: T) => ReactNode;
  rowMenuLabel?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const activates = (item: T) =>
    !!onRowActivate && (canActivateRow?.(item) ?? true);
  return (
    <Table className={`${styles.table} ${className ?? ""}`}>
      <TableCaption className="sr-only">
        {caption ?? t("Resources")}
      </TableCaption>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead
              key={column.label}
              scope="col"
              className={column.align === "right" ? "text-right" : undefined}
              aria-sort={column.ariaSort}
              data-column={column.dataColumn}
            >
              {column.header ?? column.label}
            </TableHead>
          ))}
          {rowMenu && (
            <TableHead className={styles.menuCell} scope="col">
              <span className="sr-only">{rowMenuLabel ?? t("Actions")}</span>
            </TableHead>
          )}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow
            key={"id" in item ? item.id : item.key}
            tabIndex={activates(item) ? 0 : undefined}
            className={
              activates(item)
                ? "cursor-pointer focus-visible:-outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
                : undefined
            }
            onClick={(event) => {
              if (
                event.target instanceof Element &&
                event.target.closest(
                  "button, a, input, select, textarea, [role='button'], [role='menuitem']",
                )
              )
                return;
              if (activates(item)) onRowActivate?.(item, event.currentTarget);
            }}
            onKeyDown={(event) => {
              if (
                event.target === event.currentTarget &&
                (event.key === "Enter" || event.key === " ") &&
                activates(item)
              ) {
                event.preventDefault();
                onRowActivate?.(item, event.currentTarget);
              }
            }}
          >
            {columns.map((column) => (
              <TableCell
                key={column.label}
                className={styles.cell}
                data-tone={column.tone ?? "secondary"}
                data-align={column.align}
                data-column={column.dataColumn}
              >
                <div
                  className={
                    column.align === "right" ? "flex justify-end" : undefined
                  }
                >
                  {column.render(item)}
                </div>
              </TableCell>
            ))}
            {rowMenu && (
              <TableCell className={`${styles.cell} ${styles.menuCell}`}>
                <RowMenu label={rowMenuLabel ?? t("Actions")}>
                  {rowMenu(item)}
                </RowMenu>
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function RowMenu({ label, children }: { label: string; children: ReactNode }) {
  if (!children) return null;
  return (
    <div className={styles.rowMenu}>
      <Menu>
        <MenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              type="button"
              aria-label={label}
              title={label}
            />
          }
        >
          <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
        </MenuTrigger>
        <MenuPopup align="end">{children}</MenuPopup>
      </Menu>
    </div>
  );
}
