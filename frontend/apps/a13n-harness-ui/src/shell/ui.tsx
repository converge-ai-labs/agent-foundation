import { useContext, type ReactNode } from "react";
import { Button, FormField, Input, SettingsSection } from "a13n-ui";
import { WarningCircleIcon } from "@phosphor-icons/react";
import { isConnectionError } from "../transport/client";
import { ConnectionNoticeContext } from "./connection";
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
  const connectionNotice = useContext(ConnectionNoticeContext);
  if (!error || (connectionNotice && isConnectionError(error))) return null;
  return (
    <div role="alert" className={styles.errorNotice}>
      <div className={styles.errorMessage}>
        <WarningCircleIcon size={18} aria-hidden="true" />
        <span>{error instanceof Error ? error.message : String(error)}</span>
      </div>
      {retry && (
        <Button variant="ghost" size="sm" onClick={retry}>
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
