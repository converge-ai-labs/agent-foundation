import type { ReactNode } from "react";
import styles from "./field.module.css";
export interface FieldProps {
  id: string;
  label: string;
  hideLabel?: boolean;
  hint?: string;
  error?: string;
  children: ReactNode;
}
/** Internal layout shared by labeled inputs and selection controls. */
export function Field({
  id,
  label,
  hideLabel,
  hint,
  error,
  children,
}: FieldProps) {
  return (
    <div className={styles.field}>
      <label
        htmlFor={id}
        className={hideLabel ? styles.hiddenLabel : styles.label}
      >
        {label}
      </label>
      {children}
      {hint && (
        <span id={`${id}-hint`} className={styles.hint}>
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className={styles.error}>
          {error}
        </span>
      )}
    </div>
  );
}
export function fieldDescription(
  id: string,
  hint?: string,
  error?: string,
  external?: string,
) {
  return (
    [external, hint && `${id}-hint`, error && `${id}-error`]
      .filter(Boolean)
      .join(" ") || undefined
  );
}
