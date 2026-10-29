import {
  ArrowDownIcon,
  ArrowUpIcon,
  ArrowsDownUpIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./sort-button.module.css";

type Direction = "asc" | "desc";

export const ariaSort = (
  direction?: Direction,
): "none" | "ascending" | "descending" =>
  direction === undefined
    ? "none"
    : direction === "asc"
      ? "ascending"
      : "descending";

/** The active direction is part of the name in tables and standalone navigators. */
export function SortButton({
  label,
  direction,
  onClick,
  className = "",
  title,
}: {
  label: string;
  direction?: Direction;
  onClick(): void;
  className?: string;
  title?: string;
}) {
  const { t } = useTranslation();
  const Icon =
    direction === undefined
      ? ArrowsDownUpIcon
      : direction === "asc"
        ? ArrowUpIcon
        : ArrowDownIcon;
  return (
    <button
      type="button"
      className={`${styles.button} ${className}`}
      data-active={direction !== undefined || undefined}
      aria-label={
        direction === undefined
          ? t("Sort by {{field}}", { field: label })
          : t("Sort by {{field}}, {{direction}}", {
              field: label,
              direction: t(direction === "asc" ? "Ascending" : "Descending"),
            })
      }
      aria-pressed={direction !== undefined}
      title={title}
      onClick={onClick}
    >
      {label}
      <Icon size={12} aria-hidden="true" />
    </button>
  );
}
