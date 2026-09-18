import { Button, FormField, ReadOnlyField, Textarea } from "a13n-ui";
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
    >
      <Textarea
        variant="soft"
        className={code ? styles.code : ""}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        required={required}
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
}: {
  pending: boolean;
  disabled?: boolean;
  label?: string;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <footer data-a13n-form-actions className={styles.formActions}>
      {onCancel && (
        <Button
          variant="outline"
          disabled={pending}
          onClick={onCancel}
          type="button"
        >
          {t("Cancel")}
        </Button>
      )}
      <Button
        type="submit"
        variant="default"
        loading={pending}
        disabled={disabled}
      >
        {label ?? t("Save changes")}
      </Button>
    </footer>
  );
}
