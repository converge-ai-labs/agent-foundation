import type { ComponentProps } from "react";
import styles from "./spinner.module.css";
export type SpinnerProps = Omit<ComponentProps<"span">, "children">;
/** Decorative by default; put a localized status label beside standalone spinners. */
export function Spinner({ className = "", ...props }: SpinnerProps) {
  return (
    <span
      aria-hidden="true"
      {...props}
      className={`${styles.spinner} ${className}`}
    />
  );
}
