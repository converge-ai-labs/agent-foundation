import { FormField, Input, SettingsRow } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { StatePill } from "../../shared/feedback";
import { FormActions, FormSection } from "../../shared/forms";
import { CredentialsPill, type CredentialState } from "./credentials-pill";
import { ProviderFacts } from "./provider-facts";
import styles from "./providers.module.css";

/**
 * One anatomy for every provider editor: identity, configuration and credentials,
 * optional advanced settings, then the footer.
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
    <FormSection title={t("Basic information")}>
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </FormField>
    </FormSection>
  );
}

/** Provider settings share one outlined group. */
export function ProviderGroup({
  children,
  title = "Configuration",
}: {
  children: ReactNode;
  title?: string;
}) {
  const { t } = useTranslation();
  return (
    <FormSection title={title ? t(title) : undefined}>{children}</FormSection>
  );
}

/** A provider another scope owns: the same group, stated rather than edited. */
export function ProviderReadOnly({
  enabled,
  credentials,
  configuration = {},
  schema,
  only,
  onClose,
}: {
  enabled: boolean;
  credentials: CredentialState;
  configuration?: Record<string, unknown>;
  schema?: Record<string, unknown> | null;
  only?: readonly string[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.editor}>
      <ProviderGroup>
        <ProviderFacts
          configuration={configuration}
          schema={schema}
          only={only}
        />
        <SettingsRow stackOnNarrow={false} label={t("Status")}>
          <StatePill state={enabled ? "enabled" : "disabled"} />
        </SettingsRow>
        <SettingsRow stackOnNarrow={false} label={t("Credentials")}>
          <CredentialsPill state={credentials} />
        </SettingsRow>
      </ProviderGroup>
      <FormActions dismiss pending={false} onCancel={onClose} />
    </div>
  );
}
