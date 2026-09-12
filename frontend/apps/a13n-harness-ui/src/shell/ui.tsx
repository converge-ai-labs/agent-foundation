import type { ReactNode } from "react";
import { Button, FormField, Input } from "a13n-ui";
import styles from "./workbench.module.css";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHeader}>
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
export function Panel({
  title,
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.panel}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div role="alert" className={styles.notice}>
      <span>{error instanceof Error ? error.message : String(error)}</span>
      {retry && (
        <Button variant="outline" onClick={retry}>
          Retry
        </Button>
      )}
    </div>
  );
}
export function TextField({
  label,
  value,
  onChange,
  description,
  type = "text",
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  description?: string;
  type?: string;
  disabled?: boolean;
}) {
  return (
    <FormField label={label} description={description}>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        disabled={disabled}
        autoComplete="off"
      />
    </FormField>
  );
}
export function JsonDetails({
  value,
  label = "Details",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className={styles.details}>
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
