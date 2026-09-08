import { useId } from "react";
import type { ComponentProps } from "react";
import { Field, fieldDescription } from "./field";
import styles from "./input.module.css";
export type InputProps = ComponentProps<"input"> & {
  label: string;
  hint?: string;
  error?: string;
};
export function Input({
  label,
  hint,
  error,
  id: providedId,
  className = "",
  "aria-describedby": describedBy,
  ...props
}: InputProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <input
        {...props}
        id={id}
        aria-invalid={error ? true : props["aria-invalid"]}
        aria-describedby={fieldDescription(id, hint, error, describedBy)}
        className={`${styles.input} ${className}`}
      />
    </Field>
  );
}
