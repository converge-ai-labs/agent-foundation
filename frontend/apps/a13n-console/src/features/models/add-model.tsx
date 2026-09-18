import { ModalFrame } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import {
  BrandTitle,
  CatalogStep,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { ProviderIcon, ResourceEditorButton } from "../../shared/identity";
import { connectStepDescription, connectStepTitle } from "./add-provider";
import { type ModelScope } from "./api";
import { CatalogPicker } from "./catalog-picker";
import {
  CatalogNotice,
  ModelFields,
  ModelSelection,
  ModelStatus,
  useModelDraft,
} from "./model-form";
import { ModelIcon } from "./model-icon";
import { useProviderDraft } from "./provider-draft";
import { ProviderChoice, ProviderConnect } from "./provider-setup";
import styles from "./models.module.css";

type Step = "provider" | "connect" | "model" | "details";

/**
 * Adding a model reads as three questions — which provider, which model, and
 * how it should behave — with connecting a new provider folded into the first.
 */
export function AddModel({
  scope,
  providerId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  providerId?: string;
  onSaved?: (model: Schema["Model"]) => void;
} & ResourceEditorControl) {
  const { t } = useTranslation();
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const [step, setStep] = useState<Step>(providerId ? "model" : "provider");
  const model = useModelDraft({
    scope,
    providerId,
    active: open,
    close: () => setOpen(false),
    onSaved,
  });
  const definitions = model.definitions.data?.items ?? [];
  const providers = model.providers.data ?? [];
  const providerDraft = useProviderDraft({
    scope,
    definitions,
    initialType: "",
    close: () => (providers.length ? setStep("provider") : setOpen(false)),
    onCreated: (provider, preferredApi) => {
      model.acceptProvider(provider, preferredApi);
      setStep("model");
    },
  });
  const loading = !model.providers.data || !model.definitions.data;
  // With nothing connected yet there is no list to choose from.
  const current: Step =
    step === "provider" && !loading && !providers.length ? "connect" : step;
  const error = model.providers.error ?? model.definitions.error;
  const heading = stepHeading(current, model, providerDraft.definition, t);
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton createLabel="Add model" />
        ) : undefined
      }
      size="lg"
      placement="top"
      title={heading.title}
      description={heading.description}
      closeLabel={t("Close")}
    >
      {open &&
        (error ? (
          <ErrorNotice error={error} />
        ) : loading ? (
          <Loading variant="form" rows={3} />
        ) : current === "provider" ? (
          <ProviderChoice
            providers={providers}
            definitions={definitions}
            onSelect={(id) => {
              model.chooseProvider(id);
              setStep("model");
            }}
            onConnect={() => setStep("connect")}
          />
        ) : current === "connect" ? (
          <ProviderConnect
            definitions={definitions}
            draft={providerDraft}
            onBack={providers.length ? () => setStep("provider") : undefined}
          />
        ) : current === "model" ? (
          <CatalogStep
            backLabel={t("Choose a provider")}
            onBack={providers.length ? () => setStep("provider") : undefined}
          >
            <CatalogNotice model={model} />
            {model.catalog.isPending ? (
              <Loading variant="list" rows={5} />
            ) : (
              <CatalogPicker
                entries={model.catalog.data?.items ?? []}
                channels={model.channels}
                allowCompatible={model.selectedProvider?.type === "openai"}
                providerName={model.definition?.display_name}
                value={model.draft.catalog_ref}
                onSelect={(entry) => {
                  model.chooseCatalog(entry);
                  setStep("details");
                }}
              />
            )}
          </CatalogStep>
        ) : (
          <CatalogStep
            backLabel={t("Choose a model")}
            onBack={() => setStep("model")}
          >
            <form
              className={styles.modelForm}
              onSubmit={(event) => {
                event.preventDefault();
                model.save.mutate();
              }}
            >
              <ModelSelection model={model} />
              <ModelFields model={model} />
              <ModelStatus model={model} />
              <ErrorNotice error={model.save.error} />
              <FormActions
                onCancel={() => setOpen(false)}
                pending={model.save.isPending}
                disabled={model.incomplete}
                label={t("Add model")}
              />
            </form>
          </CatalogStep>
        ))}
    </ModalFrame>
  );
}

function stepHeading(
  step: Step,
  model: ReturnType<typeof useModelDraft>,
  connecting: Schema["ModelProviderDefinition"] | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
): { title: ReactNode; description?: string } {
  if (step === "provider")
    return {
      title: t("Choose a provider"),
      description: t("Models are served by a provider you connect once."),
    };
  if (step === "connect")
    return connecting
      ? {
          title: connectStepTitle(connecting, t),
          description: connectStepDescription(connecting, t),
        }
      : {
          title: t("Connect a new provider"),
          description: t("Choose the service that hosts your models."),
        };
  if (step === "model")
    return {
      title: (
        <BrandTitle
          mark={<ProviderIcon type={model.selectedProvider?.type ?? ""} />}
        >
          {t("Choose a model")}
        </BrandTitle>
      ),
      description: t("Pick one from the catalog, or add a model by its ID."),
    };
  return {
    title: (
      <BrandTitle
        mark={
          <ModelIcon
            upstream={model.draft.upstream_model}
            catalogRef={model.draft.catalog_ref}
            provider={model.selectedProvider?.type}
            size={20}
          />
        }
      >
        {t("Details")}
      </BrandTitle>
    ),
    description: t("Name this model and set the defaults agents will use."),
  };
}
