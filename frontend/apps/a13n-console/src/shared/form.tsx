import { useId, useState, type ReactNode } from "react";
import { Button, Dialog } from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "./feedback";
import styles from "./shared.module.css";

export function TextArea({
  label,
  value,
  onChange,
  rows = 5,
  hint,
  required,
  code = false,
  hideLabel = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  hint?: string;
  required?: boolean;
  code?: boolean;
  hideLabel?: boolean;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={hideLabel ? "visually-hidden" : undefined}>
        {label}
      </label>
      <textarea
        id={id}
        className={code ? styles.code : ""}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        required={required}
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}
export function Confirm({
  title,
  description,
  action,
  onSuccess,
  trigger,
  danger = false,
  children,
}: {
  title: string;
  description: string;
  action: () => Promise<unknown>;
  onSuccess?: () => void;
  trigger: ReactNode;
  danger?: boolean;
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
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        <Button variant={danger ? "danger" : "secondary"} size="sm">
          {trigger}
        </Button>
      }
      title={title}
      description={description}
      closeLabel={t("Close")}
      footer={
        <Button
          loading={mutation.isPending}
          variant={danger ? "danger" : "primary"}
          onClick={() => mutation.mutate()}
        >
          {t("Confirm")}
        </Button>
      }
    >
      {children}
      <ErrorNotice error={mutation.error} />
    </Dialog>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className={styles.json}>{JSON.stringify(value, null, 2)}</pre>;
}
export function FormActions({
  pending,
  label,
  onCancel,
}: {
  pending: boolean;
  label?: string;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.formActions}>
      {onCancel && <Button onClick={onCancel}>{t("Cancel")}</Button>}
      <Button type="submit" variant="primary" loading={pending}>
        {label ?? t("Save changes")}
      </Button>
    </div>
  );
}
