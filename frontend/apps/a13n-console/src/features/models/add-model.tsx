import { CheckIcon, CubeIcon, PlugIcon } from "@phosphor-icons/react";
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
import { ModelPicker } from "./model-picker";
import { ModelFields, ModelSelection, useModelDraft } from "./model-form";
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
  providerId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
  requireEnabled = false,
  submitLabel,
}: {
  providerId?: string;
  requireEnabled?: boolean;
  submitLabel?: string;
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
    providerId,
    active: open,
    close: () => setOpen(false),
    onSaved,
  });
  const definitions = model.definitions.data?.items ?? [];
  const providers = model.providers.data ?? [];
  const providerDraft = useProviderDraft({
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
      onOpenChange={(value, details) => {
        if (!value && (model.save.isPending || providerDraft.save.isPending)) {
          details.cancel();
          return;
        }
        modalProps.onOpenChange(value, details);
      }}
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
      {open && !loading && !error && (
        <ol className={styles.setupSteps} aria-label={t("Add model")}>
          {[
            {
              label: "Provider",
              icon: PlugIcon,
              active: current === "provider" || current === "connect",
            },
            { label: "Model", icon: CubeIcon, active: current === "model" },
            {
              label: "Details",
              icon: CheckIcon,
              active: current === "details",
            },
          ].map(({ label, icon: Icon, active }) => (
            <li key={label} aria-current={active ? "step" : undefined}>
              <Icon size={16} aria-hidden="true" />
              <span>{t(label)}</span>
            </li>
          ))}
        </ol>
      )}
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
            <ModelPicker model={model} onSelected={() => setStep("details")} />
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
              <ModelSelection model={model} showAddress={model.manual} />
              <ModelFields
                model={model}
                compact
                requireEnabled={requireEnabled}
              />
              <ErrorNotice error={model.save.error} />
              <FormActions
                onCancel={() => setOpen(false)}
                pending={model.save.isPending}
                disabled={model.incomplete}
                label={submitLabel ?? t("Add model")}
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
  connecting: Schema["ProviderType"] | undefined,
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
      description: t(
        model.definition?.supports_model_discovery
          ? "Choose a model available to this provider, or enter a model ID."
          : "Pick one from the catalog, or add a model by its ID.",
      ),
    };
  const chosen =
    model.selectedEntry?.name || model.draft.name || model.draft.model_name;
  return {
    title: (
      <BrandTitle
        mark={
          <ModelIcon
            upstream={model.draft.model_name}
            catalogRef={model.draft.catalog_ref}
            provider={model.selectedProvider?.type}
            size={20}
          />
        }
      >
        {chosen ? t("Add {{model}}", { model: chosen }) : t("Add model")}
      </BrandTitle>
    ),
    description: undefined,
  };
}
