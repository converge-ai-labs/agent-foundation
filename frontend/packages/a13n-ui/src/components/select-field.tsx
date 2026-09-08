import { useId } from "react";
import { Field, fieldDescription } from "./field";
import { Select, type SelectProps } from "./select";
export type SelectFieldProps = SelectProps & { hint?: string; error?: string };
export function SelectField({
  id: providedId,
  label,
  hint,
  error,
  "aria-describedby": describedBy,
  ...props
}: SelectFieldProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <Select
        {...props}
        fullWidth
        id={id}
        label={label}
        aria-invalid={error ? true : props["aria-invalid"]}
        aria-describedby={fieldDescription(id, hint, error, describedBy)}
      />
    </Field>
  );
}
