import type { ComponentProps } from "react";
import styles from "./badge.module.css";
export type BadgeProps = ComponentProps<"span"> & {
  tone?: "neutral" | "success" | "warning" | "danger";
};
export function Badge({
  tone = "neutral",
  className = "",
  ...props
}: BadgeProps) {
  return (
    <span
      {...props}
      className={`${styles.badge} ${className}`}
      data-tone={tone}
    />
  );
}
