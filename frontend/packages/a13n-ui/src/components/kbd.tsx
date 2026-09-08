import type { ComponentProps } from "react";
import styles from "./kbd.module.css";
/** A visual hint, not a shortcut registration. The owner implements the action. */
export function Kbd({ className = "", ...props }: ComponentProps<"kbd">) {
  return <kbd {...props} className={`${styles.kbd} ${className}`} />;
}
