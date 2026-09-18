import { PlusIcon } from "@phosphor-icons/react";
import { Button, FormField, SearchPicker } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import {
  ProviderCatalog,
  ProviderConnectForm,
  connectStepDescription,
} from "./add-provider";
import { type ModelScope } from "./api";
import { useProviderDraft } from "./provider-draft";

/**
 * Provider choice inside the model form. Connecting a new provider reuses the
 * same catalog and connect form as the Add provider dialog.
 */
export function ProviderSetup({
  scope,
  providers,
  definitions,
  value,
  error,
  onSelect,
  onCreated,
  onCancel,
}: {
  scope: ModelScope;
  providers?: Schema["ModelProvider"][];
  definitions?: Schema["ModelProviderDefinition"][];
  value: string;
  error?: unknown;
  onSelect: (id: string) => void;
  onCreated: (provider: Schema["ModelProvider"], modelApi?: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(),
    [connecting, setConnecting] = useState(false);
  if (error) return <ErrorNotice error={error} />;
  if (!providers || !definitions) return <Loading variant="form" rows={3} />;
  if (connecting || !providers.length)
    return (
      <ConnectProvider
        scope={scope}
        definitions={definitions}
        onBack={providers.length ? () => setConnecting(false) : undefined}
        onCancel={() => (providers.length ? setConnecting(false) : onCancel())}
        onCreated={onCreated}
      />
    );
  return (
    <div className={styles.stack}>
      <FormField
        label={t("Provider")}
        labelAction={
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setConnecting(true)}
          >
            <PlusIcon size={14} />
            {t("Connect provider")}
          </Button>
        }
      >
        <SearchPicker
          label={t("Provider")}
          placeholder={t("Choose a provider…")}
          emptyMessage={t("No results")}
          value={value}
          onValueChange={onSelect}
          groups={[
            {
              label: t("Providers"),
              options: providers.map((item) => ({
                value: item.id,
                label: item.name,
                icon: <ProviderIcon type={item.type} />,
                description:
                  definitions.find(
                    (definition) => definition.type === item.type,
                  )?.display_name ?? item.type,
                disabled: !item.enabled,
              })),
            },
          ]}
        />
      </FormField>
    </div>
  );
}

function ConnectProvider({
  scope,
  definitions,
  onBack,
  onCancel,
  onCreated,
}: {
  scope: ModelScope;
  definitions: Schema["ModelProviderDefinition"][];
  onBack?: () => void;
  onCancel: () => void;
  onCreated: (provider: Schema["ModelProvider"], modelApi?: string) => void;
}) {
  const { t } = useTranslation();
  const draft = useProviderDraft({
    scope,
    definitions,
    initialType: "",
    close: onCancel,
    onCreated,
  });
  const chosen = draft.definition;
  if (!chosen)
    return (
      <div className={styles.stack}>
        {onBack && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="justify-self-start"
            onClick={onBack}
          >
            {t("Choose provider")}
          </Button>
        )}
        <ProviderCatalog
          definitions={definitions}
          onChoose={draft.chooseType}
        />
      </div>
    );
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>{connectStepDescription(chosen, t)}</p>
      <ProviderConnectForm
        draft={draft}
        definition={chosen}
        onBack={() => draft.chooseType("")}
        submitLabel={t("Connect provider")}
      />
    </div>
  );
}
