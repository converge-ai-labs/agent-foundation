import type { ReactNode } from "react";
import { Button, FormField, Input, SettingsSection } from "a13n-ui";
import { WarningCircle } from "@phosphor-icons/react";
import styles from "./workbench.module.css";

export function PageHeader({
  title,
  description,
  actions,
  level = 1,
}: {
  level?: 1 | 2;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <header className={styles.pageHeader}>
      <div>
        <Heading>{title}</Heading>
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
    <SettingsSection title={title}>
      <div className={styles.panelContent}>{children}</div>
    </SettingsSection>
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
      <div className={styles.actions}>
        <WarningCircle size={18} aria-hidden="true" />
        <span>{error instanceof Error ? error.message : String(error)}</span>
      </div>
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
