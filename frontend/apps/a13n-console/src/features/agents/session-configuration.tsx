import { Button, DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SessionPolicyFields } from "../credentials/session-policy-fields";
import type { AgentConfig } from "./configuration";
import shared from "../../shared/shared.module.css";
import styles from "../credentials/credentials.module.css";
import agentStyles from "./agents.module.css";

export function AgentSessionConfiguration({
  value,
  onChange,
  readOnly,
  invalid,
}: {
  value: AgentConfig["default_session_configuration"];
  onChange: (value: AgentConfig["default_session_configuration"]) => void;
  readOnly: boolean;
  invalid: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(!!value);
  return (
    <DisclosureSection
      className={agentStyles.environmentSession}
      title={t("Default Session configuration")}
      summary={value ? t("Configured") : t("Defaults")}
      open={open || invalid}
      onOpenChange={setOpen}
    >
      <div className={shared.stack}>
        <div className={agentStyles.environmentSessionOverview}>
          <div>
            {!value && (
              <p className={agentStyles.environmentSessionHint}>
                {t("No Session defaults configured.")}
              </p>
            )}
            <p className={agentStyles.environmentSessionHint}>
              {t(
                "Used only when creating a new primary environment binding. Existing conversation bindings keep their own configuration.",
              )}
            </p>
          </div>
          {!value && !readOnly && (
            <Button
              type="button"
              variant="outline"
              className={styles.inlineAction}
              size="sm"
              onClick={() => onChange({})}
            >
              {t("Configure Session defaults")}
            </Button>
          )}
        </div>
        {value && (
          <>
            <SessionPolicyFields
              value={value}
              onChange={onChange}
              readOnly={readOnly}
            />
            {!readOnly && (
              <Button
                type="button"
                variant="ghost"
                className={styles.inlineAction}
                size="sm"
                onClick={() => onChange(null)}
              >
                {t("Remove Session defaults")}
              </Button>
            )}
          </>
        )}
      </div>
    </DisclosureSection>
  );
}
