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
  render: (item: T) => ReactNode;
}
export function ResourceTable<T extends { id: string }>({
  items,
  columns,
  caption,
}: {
  items: readonly T[];
  columns: readonly ResourceColumn<T>[];
  caption?: string;
}) {
  const { t } = useTranslation();
  return (
    <Table>
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
          <TableRow key={item.id}>
            {columns.map((column) => (
              <TableCell
                key={column.label}
                className="leading-normal [&_small]:mt-1 [&_small]:block [&_small]:text-muted-foreground"
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
