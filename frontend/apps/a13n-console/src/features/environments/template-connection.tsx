import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { EnvironmentProviders } from "./providers";
import {
  PageActionsTarget,
  PageActionClaimed,
  PageEmptyAction,
} from "../../shared/page/page-actions";
import styles from "./environments.module.css";

/** Manage providers without discarding the surrounding template draft. */
export function TemplateConnection({
  definition,
  providers,
  value,
  onChange,
}: {
  definition: Schema["ProviderType"];
  providers: Schema["Provider"][];
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [managing, setManaging] = useState(false);
  const choices = providers.filter(
    (item) => item.enabled && item.type === definition.type,
  );
  return (
    <div className={styles.connectionBlock}>
      <div className={styles.connectionRow}>
        <ChoiceField
          label={t("Provider")}
          placeholder={t("Select a provider")}
          value={value}
          onValueChange={onChange}
          options={choices.map((item) => ({
            value: item.id,
            label: item.name,
          }))}
        />
        <ModalFrame
          open={managing}
          onOpenChange={setManaging}
          trigger={
            <Button type="button" variant="outline">
              {t("Manage providers")}
            </Button>
          }
          title={t("Manage providers")}
          description={t(
            "View and edit providers, or delete unused ones. Your template draft stays here.",
          )}
          closeLabel={t("Close")}
          size="lg"
        >
          {managing && (
            <PageActionsTarget value={null}>
              <PageActionClaimed value={false}>
                <PageEmptyAction value={null}>
                  <EnvironmentProviders />
                </PageEmptyAction>
              </PageActionClaimed>
            </PageActionsTarget>
          )}
        </ModalFrame>
      </div>
      {!choices.length && (
        <p className={styles.panelNote}>
          {t(
            "No enabled provider for this runtime yet. Open Manage providers to add one; your template draft stays here.",
          )}
        </p>
      )}
    </div>
  );
}
