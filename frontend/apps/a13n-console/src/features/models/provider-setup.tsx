import { PlusIcon } from "@phosphor-icons/react";
import { StatusPill } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import {
  CatalogStep,
  DirectoryGroup,
  DirectoryList,
  DirectoryRow,
} from "../../shared/dialogs";
import { ProviderIcon } from "../../shared/identity";
import { ModelProviderCatalog, ProviderConnectForm } from "./add-provider";
import { type useProviderDraft } from "./provider-draft";

type Definition = Schema["ModelProviderMetadata"];

/**
 * First step of the add flow: the providers this scope already connects, with
 * a way to connect another one without leaving the dialog.
 */
export function ProviderChoice({
  providers,
  definitions,
  onSelect,
  onConnect,
}: {
  providers: readonly Schema["ModelProvider"][];
  definitions: readonly Definition[];
  onSelect: (id: string) => void;
  onConnect: () => void;
}) {
  const { t } = useTranslation();
  return (
    <DirectoryList>
      <DirectoryRow
        tone="elevated"
        icon={<PlusIcon size={18} aria-hidden="true" />}
        name={t("Connect a new provider")}
        detail={t("OpenAI, a gateway, or any OpenAI-compatible endpoint")}
        onClick={onConnect}
      />
      <DirectoryGroup label={t("Connected providers")}>
        {providers.map((provider) => (
          <DirectoryRow
            key={provider.id}
            icon={<ProviderIcon type={provider.type} />}
            name={provider.name}
            detail={
              definitions.find((item) => item.type === provider.type)
                ?.display_name ?? provider.type
            }
            disabled={!provider.enabled}
            meta={
              provider.enabled ? undefined : (
                <StatusPill variant="neutral">{t("Disabled")}</StatusPill>
              )
            }
            onClick={() => onSelect(provider.id)}
          />
        ))}
      </DirectoryGroup>
    </DirectoryList>
  );
}

/**
 * Connecting a provider inline reuses the catalog and the connect form of the
 * Add provider dialog, so both routes ask for the same things.
 */
export function ProviderConnect({
  definitions,
  draft,
  onBack,
}: {
  definitions: Definition[];
  draft: ReturnType<typeof useProviderDraft>;
  onBack?: () => void;
}) {
  const { t } = useTranslation();
  if (!draft.definition)
    return (
      <CatalogStep backLabel={t("Choose a provider")} onBack={onBack}>
        <ModelProviderCatalog
          definitions={definitions}
          onChoose={draft.chooseType}
        />
      </CatalogStep>
    );
  return (
    <ProviderConnectForm
      draft={draft}
      definition={draft.definition}
      onBack={() => draft.chooseType("")}
      backLabel={t("All providers")}
      submitLabel={t("Connect provider")}
    />
  );
}
