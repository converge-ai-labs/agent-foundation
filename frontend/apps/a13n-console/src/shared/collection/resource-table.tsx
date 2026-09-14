import styles from "./resource-table.module.css";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface ResourceColumn<T> {
  label: string;
  align?: "left" | "right";
  tone?: "primary" | "secondary" | "muted";
  render: (item: T) => ReactNode;
}
export function ResourceTable<T extends { id: string }>({
  items,
  columns,
  caption,
  onRowActivate,
  canActivateRow,
}: {
  items: readonly T[];
  columns: readonly ResourceColumn<T>[];
  caption?: string;
  onRowActivate?: (item: T, element: HTMLElement) => void;
  canActivateRow?: (item: T) => boolean;
}) {
  const { t } = useTranslation();
  return (
    <Table className={styles.table}>
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
            >
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow
            key={item.id}
            tabIndex={
              onRowActivate && (canActivateRow?.(item) ?? true) ? 0 : undefined
            }
            className={
              onRowActivate && (canActivateRow?.(item) ?? true)
                ? "cursor-pointer hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2"
                : undefined
            }
            onClick={(event) => {
              if (
                event.target instanceof Element &&
                event.target.closest(
                  "button, a, input, select, textarea, [role='button']",
                )
              )
                return;
              if (onRowActivate && (canActivateRow?.(item) ?? true))
                onRowActivate(item, event.currentTarget);
            }}
            onKeyDown={(event) => {
              if (
                event.target === event.currentTarget &&
                (event.key === "Enter" || event.key === " ") &&
                onRowActivate &&
                (canActivateRow?.(item) ?? true)
              ) {
                event.preventDefault();
                onRowActivate(item, event.currentTarget);
              }
            }}
          >
            {columns.map((column) => (
              <TableCell
                key={column.label}
                className={styles.cell}
                data-tone={column.tone ?? "secondary"}
                data-align={column.align}
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
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
