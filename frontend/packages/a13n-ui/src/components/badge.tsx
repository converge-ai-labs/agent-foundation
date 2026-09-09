import type { ComponentProps } from "react";
import styles from "./badge.module.css";
export type BadgeProps = ComponentProps<"span"> & {
  tone?: "neutral" | "success" | "warning" | "danger";
  variant?: "default" | "status";
};
export function Badge({
  tone = "neutral",
  variant = "default",
  className = "",
  ...props
}: BadgeProps) {
  return (
    <span
      {...props}
      className={`${styles.badge} ${className}`}
      data-tone={tone}
      data-variant={variant}
    />
  );
}
