import { Button, FormField, ReadOnlyField, Textarea } from "a13n-ui";
import type { ReactNode, Ref } from "react";
import { useTranslation } from "react-i18next";
import styles from "./forms.module.css";

/**
 * Long-form text (instructions, prompts, JSON) uses the soft surface textarea
 * so the editor reads as content rather than as a boxed control.
 */
export function TextAreaField({
  label,
  value,
  onChange,
  rows = 5,
  hint,
  placeholder,
  required,
  code = false,
  hideLabel = false,
  readOnly = false,
  disabled = false,
  maxLength,
  error,
  invalid,
  ref,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  hint?: string;
  placeholder?: string;
  required?: boolean;
  code?: boolean;
  hideLabel?: boolean;
  readOnly?: boolean;
  disabled?: boolean;
  maxLength?: number;
  error?: string;
  invalid?: boolean;
  ref?: Ref<HTMLTextAreaElement>;
}) {
  if (readOnly)
    return (
      <ReadOnlyField label={label} description={hint} hideLabel={hideLabel}>
        <div className={styles.textValue}>
          {code ? (
            <pre className={styles.codeValue}>{value || "—"}</pre>
          ) : (
            value || "—"
          )}
        </div>
      </ReadOnlyField>
    );
  return (
    <FormField
      label={label}
      description={hint}
      hideLabel={hideLabel}
      error={error}
      invalid={invalid}
      disabled={disabled}
    >
      <Textarea
        ref={ref}
        aria-invalid={invalid || undefined}
        className={code ? styles.code : ""}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        placeholder={placeholder}
        required={required}
        maxLength={maxLength}
      />
    </FormField>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className={`${styles.json} a13n-scrollbar`}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function FormActions({
  pending,
  label,
  onCancel,
  disabled = false,
  variant = "default",
  leading,
  beforeCancel,
  cancelLabel,
  dismiss = false,
}: {
  pending: boolean;
  disabled?: boolean;
  label?: string;
  onCancel?: () => void;
  cancelLabel?: string;
  /** A read-only dialog dismisses instead of submitting. */
  dismiss?: boolean;
  /** Outline keeps a secondary submit row from competing with the page's
   *  one filled primary action. */
  variant?: "default" | "outline";
  /** Actions that belong to the form but not to its outcome, at the left. */
  leading?: ReactNode;
  /** Destructive or secondary actions immediately before Cancel. */
  beforeCancel?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <footer
      data-a13n-form-actions
      className={styles.formActions}
      data-leading={leading ? "" : undefined}
    >
      {leading && <div className={styles.formActionsLeading}>{leading}</div>}
      <div className={styles.formActionsPrimary}>
        {beforeCancel}
        {onCancel && (
          <Button
            variant="outline"
            disabled={pending}
            onClick={onCancel}
            type="button"
          >
            {cancelLabel ?? t(dismiss ? "Close" : "Cancel")}
          </Button>
        )}
        {!dismiss && (
          <Button
            type="submit"
            variant={variant}
            loading={pending}
            disabled={disabled}
          >
            {label ?? t("Save changes")}
          </Button>
        )}
      </div>
    </footer>
  );
}
