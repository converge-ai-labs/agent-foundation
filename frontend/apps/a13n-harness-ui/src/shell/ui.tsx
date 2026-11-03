import { useTranslation } from "../i18n";
import { errorMessage } from "../i18n/error-message";
import { SettingsSection } from "./settings";
import { useContext, type ReactNode, type FocusEventHandler } from "react";
import { Button, FormField, Input } from "a13n-ui";
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
  description?: string;
  actions?: ReactNode;
}) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <header className={styles.pageHeader}>
      <div>
        <Heading>{title}</Heading>
        {description && <p>{description}</p>}
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
  message,
  inline = false,
}: {
  error: unknown;
  retry?: () => void;
  /** Display text; the original error still owns recovery classification. */
  message?: string;
  /** One quiet line for secondary content, without the notice frame. */
  inline?: boolean;
}) {
  const { t } = useTranslation();
  const connectionNotice = useContext(ConnectionNoticeContext);
  if (!error || (connectionNotice && isConnectionError(error))) return null;
  return (
    <div
      role="alert"
      className={inline ? styles.errorInline : styles.errorNotice}
    >
      <div className={styles.errorMessage}>
        <WarningCircleIcon size={inline ? 14 : 18} aria-hidden="true" />
        <span>{message ?? errorMessage(error, t)}</span>
      </div>
      {retry && (
        <Button variant="ghost" size="sm" onClick={retry}>
          {t("Retry")}
        </Button>
      )}
    </div>
  );
}
/** A compact list filter: the label is announced, the placeholder shows it. */
export function SearchField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <Input
      type="search"
      aria-label={label}
      placeholder={placeholder ?? `${label}…`}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      autoComplete="off"
      className={styles.search}
    />
  );
}
/** The placeholder a list shows when it has no items yet. */
export function EmptyList({ children }: { children: ReactNode }) {
  return <p className={styles.emptyList}>{children}</p>;
}
export function TextField({
  label,
  value,
  onChange,
  description,
  type = "text",
  disabled = false,
  autoFocus = false,
  onFocus,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  description?: string;
  type?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  onFocus?: FocusEventHandler<HTMLInputElement>;
}) {
  return (
    <FormField label={label} description={description}>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        disabled={disabled}
        autoFocus={autoFocus}
        onFocus={onFocus}
        autoComplete="off"
      />
    </FormField>
  );
}
