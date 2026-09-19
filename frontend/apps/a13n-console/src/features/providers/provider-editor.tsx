import { FormField, Input, SettingsRow, SettingsSection } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { StatePill } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { CredentialsPill, type CredentialState } from "./credentials-pill";
import { ProviderFacts } from "./provider-facts";
import styles from "./providers.module.css";

/**
 * One anatomy for every provider editor: the name it is listed under, one soft
 * settings group, whatever the category folds away, then the footer.
 */
export function ProviderEditor({
  onSubmit,
  children,
}: {
  onSubmit: (event: React.FormEvent) => void;
  children: ReactNode;
}) {
  return (
    <form className={styles.editor} onSubmit={onSubmit}>
      {children}
    </form>
  );
}

/** The body of an editor that stops accepting input while it saves. */
export function ProviderEditorFields({
  disabled = false,
  children,
}: {
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <fieldset disabled={disabled} className={styles.editorFields}>
      {children}
    </fieldset>
  );
}

export function ProviderName({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <FormField
      className="min-w-0 w-full"
      label={t("Name")}
      description={t("How this provider is listed across the console.")}
    >
      <Input
        required
        maxLength={128}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </FormField>
  );
}

/** The single soft group an editor settles everything else into. */
export function ProviderGroup({
  note,
  children,
}: {
  /** A sentence the group cannot say in a row, such as an immutable target. */
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={styles.group}>
      <SettingsSection>{children}</SettingsSection>
      {note && <p className={styles.groupNote}>{note}</p>}
    </div>
  );
}

/**
 * A provider another scope owns, or one the deployment configures: the same
 * group, stated rather than edited.
 */
export function ProviderReadOnly({
  enabled,
  credentials,
  configuration = {},
  schema,
  only,
  hideDefaults = false,
  facts,
  note,
  leading,
  onClose,
}: {
  enabled: boolean;
  credentials: CredentialState;
  configuration?: Record<string, unknown>;
  schema?: Record<string, unknown> | null;
  only?: readonly string[];
  hideDefaults?: boolean;
  /** Rows the category states itself, such as a live engine status. */
  facts?: ReactNode;
  note?: ReactNode;
  leading?: ReactNode;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.editor}>
      <ProviderGroup note={note}>
        {facts}
        <ProviderFacts
          configuration={configuration}
          schema={schema}
          only={only}
          hideDefaults={hideDefaults}
        />
        <SettingsRow stackOnNarrow={false} label={t("Status")}>
          <StatePill state={enabled ? "enabled" : "disabled"} />
        </SettingsRow>
        <SettingsRow stackOnNarrow={false} label={t("Credentials")}>
          <CredentialsPill state={credentials} />
        </SettingsRow>
      </ProviderGroup>
      <FormActions
        dismiss
        pending={false}
        leading={leading}
        cancelLabel={t("Close")}
        onCancel={onClose}
      />
    </div>
  );
}
