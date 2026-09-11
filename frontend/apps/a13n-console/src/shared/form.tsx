import {
  Button,
  type ButtonProps,
  FormField,
  ModalFrame,
  ReadOnlyField,
  Textarea,
} from "a13n-ui";

import { useState, type ReactElement, type ReactNode } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "./feedback";
import styles from "./shared.module.css";

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
        className={code ? styles.code : ""}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        required={required}
      />
    </FormField>
  );
}
export function Confirm({
  title,
  description,
  action,
  onSuccess,
  trigger,
  triggerElement,
  danger = false,
  triggerVariant,
  children,
}: {
  title: string;
  description: string;
  action: () => Promise<unknown>;
  onSuccess?: () => void;
  trigger?: ReactNode;
  triggerElement?: ReactElement;
  danger?: boolean;
  triggerVariant?: ButtonProps["variant"];
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const mutation = useMutation({
    mutationFn: action,
    onSuccess: () => {
      if (onSuccess) onSuccess();
      else void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        triggerElement ?? (
          <Button
            variant={triggerVariant ?? (danger ? "destructive" : "outline")}
            className={
              triggerVariant === "ghost"
                ? danger
                  ? "font-normal text-destructive-foreground"
                  : "font-normal text-muted-foreground"
                : undefined
            }
            size="sm"
            type="button"
          >
            {trigger}
          </Button>
        )
      }
      size={"md"}
      title={title}
      description={description}
      closeLabel={t("Close")}
      footer={
        <>
          <Button
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => setOpen(false)}
            type="button"
          >
            {t("Cancel")}
          </Button>
          <Button
            variant={danger ? "destructive" : "default"}
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
            type="button"
          >
            {t("Confirm")}
          </Button>
        </>
      }
      open={open}
    >
      {(children || mutation.error) && (
        <>
          {children}
          <ErrorNotice error={mutation.error} />
        </>
      )}
    </ModalFrame>
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
