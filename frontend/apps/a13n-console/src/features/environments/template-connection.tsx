import { PlusIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { BrandTitle } from "../../shared/dialogs";
import { ProviderIcon } from "../../shared/identity";
import { ProviderForm } from "../providers";
import styles from "./environments.module.css";

/** Connection creation owns its credentials; closing unmounts and discards that draft. */
export function TemplateConnection({
  definition,
  providers,
  value,
  onChange,
  onCreated,
}: {
  definition: Schema["ProviderType"];
  providers: Schema["Provider"][];
  value: string;
  onChange: (id: string) => void;
  onCreated: (provider: Schema["Provider"]) => void;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  const [open, setOpen] = useState(false);
  const choices = providers.filter(
    (item) => item.enabled && item.type === definition.type,
  );
  return (
    <div className={styles.connectionBlock}>
      <div className={styles.connectionRow}>
        <ChoiceField
          label={t("Connection")}
          placeholder={t("Select a connection")}
          value={value}
          onValueChange={onChange}
          options={choices.map((item) => ({
            value: item.id,
            label: item.name,
          }))}
        />
        {can("write") && (
          <ModalFrame
            open={open}
            onOpenChange={setOpen}
            trigger={
              <Button type="button" variant="outline">
                <PlusIcon aria-hidden="true" />
                {t("Add connection")}
              </Button>
            }
            size="lg"
            title={
              <BrandTitle mark={<ProviderIcon type={definition.type} />}>
                {t("Connect {{provider}}", {
                  provider: definition.display_name,
                })}
              </BrandTitle>
            }
            description={t(
              "This connection is saved separately and remains available if you cancel template creation.",
            )}
            closeLabel={t("Close")}
          >
            {open && (
              <ProviderForm
                kind="environment"
                createLabel={t("Add connection")}
                definition={definition}
                definitions={[definition]}
                onCreated={onCreated}
                close={() => setOpen(false)}
                reload={async () => {}}
              />
            )}
          </ModalFrame>
        )}
      </div>
      {!choices.length && (
        <p className={styles.panelNote}>
          {t(
            "No enabled connection for this runtime yet. Add one to continue; your template draft stays here.",
          )}
        </p>
      )}
    </div>
  );
}
