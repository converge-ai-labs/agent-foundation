import { Button, FormField, ReadOnlyField, Textarea } from "a13n-ui";
import type { ReactNode } from "react";
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
  required,
  code = false,
  hideLabel = false,
  readOnly = false,
  disabled = false,
  maxLength,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  hint?: string;
  required?: boolean;
  code?: boolean;
  hideLabel?: boolean;
  readOnly?: boolean;
  disabled?: boolean;
  maxLength?: number;
  error?: string;
}) {
  if (readOnly)
    return (
      <ReadOnlyField label={label} description={hint} hideLabel={hideLabel}>
        {code ? (
          <pre className={styles.codeValue}>{value || "—"}</pre>
        ) : (
          value || "—"
        )}
      </ReadOnlyField>
    );
  return (
    <FormField
      label={label}
      description={hint}
      hideLabel={hideLabel}
      error={error}
      disabled={disabled}
    >
      <Textarea
        variant="soft"
        className={code ? styles.code : ""}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
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
        {onCancel && (
          <Button
            variant="outline"
            disabled={pending}
            onClick={onCancel}
            type="button"
          >
            {cancelLabel ?? t("Cancel")}
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
