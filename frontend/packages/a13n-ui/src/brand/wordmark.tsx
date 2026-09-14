import type { ComponentProps } from "react";
import styles from "./wordmark.module.css";

export type WordmarkProps = Omit<ComponentProps<"span">, "children">;

export function Wordmark({ className = "", ...props }: WordmarkProps) {
  return (
    <span {...props} className={`${styles.wordmark} ${className}`}>
      a13n
    </span>
  );
}
