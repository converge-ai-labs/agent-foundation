import type { ComponentProps, ReactNode } from "react";
import { Spinner } from "./spinner";
import styles from "./button.module.css";
export type ButtonProps = ComponentProps<"button"> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  icon?: ReactNode;
  loading?: boolean;
  loadingLabel?: string;
};
export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  loading,
  loadingLabel,
  disabled,
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${styles.button} ${className}`}
      data-variant={variant}
      data-size={size}
      data-icon-only={children == null && !loadingLabel ? true : undefined}
    >
      {(icon || loading !== undefined || loadingLabel) && (
        <span className={styles.icon} aria-hidden="true">
          {loading ? <Spinner /> : icon}
        </span>
      )}
      <span className={styles.labels}>
        <span
          className={loading && loadingLabel ? styles.reserve : undefined}
          aria-hidden={loading && loadingLabel ? true : undefined}
        >
          {children}
        </span>
        {loadingLabel && (
          <span
            className={loading ? undefined : styles.reserve}
            aria-hidden={loading ? undefined : true}
          >
            {loadingLabel}
          </span>
        )}
      </span>
    </button>
  );
}
