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
import { ManageProvidersLink } from "../providers";
import { ModelProviderCatalog, ProviderConnectForm } from "./add-provider";
import { ModelProviderIcon, providerBrand } from "./model-icon";
import { type useProviderDraft } from "./provider-draft";

type Definition = Schema["ProviderType"];

/**
 * What a row adds to a Provider's name: the endpoint an OpenAI-compatible
 * Provider calls, or the service, unless the name already says it.
 */
function providerDetail(
  provider: Schema["Provider"],
  definitions: readonly Definition[],
) {
  if (!providerBrand(provider)) {
    const url = String(provider.config.base_url);
    try {
      return new URL(url).host;
    } catch {
      return url;
    }
  }
  const service =
    definitions.find((item) => item.type === provider.type)?.display_name ??
    provider.type;
  return provider.name.toLocaleLowerCase().includes(service.toLocaleLowerCase())
    ? undefined
    : service;
}

/**
 * First step of the add flow: the providers this scope already connects, with
 * a way to connect another one without leaving the dialog. Enabled ones come
 * first, by name; a disabled one stays listed with the way to enable it.
 */
export function ProviderChoice({
  providers,
  definitions,
  onSelect,
  onConnect,
}: {
  providers: readonly Schema["Provider"][];
  definitions: readonly Definition[];
  onSelect: (id: string) => void;
  onConnect: () => void;
}) {
  const { t } = useTranslation();
  const sorted = [...providers].sort(
    (a, b) =>
      Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name),
  );
  return (
    <DirectoryList
      footer={
        sorted.some((provider) => !provider.enabled) && (
          <>
            <span>
              {t("A disabled provider can't serve models until it is enabled.")}
            </span>
            <ManageProvidersLink variant="ghost" category="models" />
          </>
        )
      }
    >
      <DirectoryRow
        tone="elevated"
        icon={<PlusIcon size={18} aria-hidden="true" />}
        name={t("Connect a new provider")}
        detail={t("OpenAI, a gateway, or any OpenAI-compatible endpoint")}
        onClick={onConnect}
      />
      <DirectoryGroup label={t("Connected providers")}>
        {sorted.map((provider) => (
          <DirectoryRow
            key={provider.id}
            icon={<ModelProviderIcon provider={provider} />}
            name={provider.name}
            detail={providerDetail(provider, definitions)}
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
