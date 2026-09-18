import { PlusIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import {
  BrandTitle,
  CatalogStep,
  CatalogTile,
  CatalogTiles,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { PlatformIcon, usePlatformName } from "../integrations/platform";
import { useAccountProviders } from "./data";
import { AccountForm, accountProviderLabels } from "./form";

type Definition = Schema["AccountProviderDefinition"];

function providerValue(definition: Definition) {
  return `${definition.provider_key}@${definition.config_version}`;
}

function useProviderLabel() {
  const platformName = usePlatformName();
  return (definition: Definition) =>
    definition.provider_key === "lark"
      ? platformName("lark")
      : (accountProviderLabels[providerValue(definition)] ??
        `${definition.provider_key} · ${definition.config_version}`);
}

/** Catalog-first account creation: choose the service, then configure it. */
export function AddApplicationAccount({
  onCreated,
}: {
  onCreated: (account: Schema["Account"]) => void;
}) {
  const { t } = useTranslation();
  const providerLabel = useProviderLabel();
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<Definition>();
  const definitions = useAccountProviders();
  function change(value: boolean) {
    setOpen(value);
    if (!value) setChosen(undefined);
  }
  return (
    <ModalFrame
      open={open}
      onOpenChange={change}
      size="lg"
      placement="top"
      closeLabel={t("Close")}
      trigger={
        <Button type="button">
          <PlusIcon aria-hidden="true" />
          {t("Add account")}
        </Button>
      }
      title={
        chosen ? (
          <BrandTitle mark={<PlatformIcon type={chosen.provider_key} />}>
            {t("Connect {{provider}}", { provider: providerLabel(chosen) })}
          </BrandTitle>
        ) : (
          t("Add application account")
        )
      }
      description={t(
        chosen
          ? "Enter the installation identifiers and credentials for this account."
          : "Connect an external account and choose how incoming events reach your agents.",
      )}
    >
      {open &&
        (definitions.error ? (
          <ErrorNotice error={definitions.error} />
        ) : definitions.isPending ? (
          <Loading variant="form" rows={3} />
        ) : chosen ? (
          <CatalogStep
            backLabel={t("All services")}
            onBack={() => setChosen(undefined)}
          >
            <AccountForm
              key={providerValue(chosen)}
              chosenProvider={providerValue(chosen)}
              onCancel={() => change(false)}
              onSuccess={(account) => {
                change(false);
                onCreated(account);
              }}
            />
          </CatalogStep>
        ) : (
          <CatalogTiles empty={t("No account providers are available.")}>
            {definitions.data?.items.length
              ? definitions.data.items.map((definition) => (
                  <CatalogTile
                    key={providerValue(definition)}
                    icon={<PlatformIcon type={definition.provider_key} />}
                    name={providerLabel(definition)}
                    onClick={() => setChosen(definition)}
                  />
                ))
              : undefined}
          </CatalogTiles>
        ))}
    </ModalFrame>
  );
}
