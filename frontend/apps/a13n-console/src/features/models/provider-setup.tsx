import { ArrowLeftIcon, PlusIcon } from "@phosphor-icons/react";
import { Button, FormField, SearchPicker } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import { type ModelScope } from "./api";
import { ProviderForm } from "./provider-form";
import modelStyles from "./models.module.css";

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
      <div className={styles.stack}>
        {!!providers.length && (
          <Button
            type="button"
            variant="ghost"
            className={modelStyles.backButton}
            onClick={() => setConnecting(false)}
          >
            <ArrowLeftIcon size={14} />
            {t("Choose provider")}
          </Button>
        )}
        <ProviderForm
          scope={scope}
          definitions={definitions}
          reload={async () => {}}
          close={() => (providers.length ? setConnecting(false) : onCancel())}
          onCreated={onCreated}
        />
      </div>
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
