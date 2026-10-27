import { WarningIcon } from "@phosphor-icons/react";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { EnabledField, FormSection } from "../../shared/forms";
import { IconTile } from "../../shared/identity";
import sharedStyles from "../../shared/shared.module.css";
import type { ModelDraft } from "./model-form";
import { ModelIcon, providerBrand } from "./model-icon";
import { ImageInputFields } from "./image-input-fields";
import { imageInputDraft } from "./image-input";
import { ModelInformation, ModelCapabilitySummary } from "./model-information";
import { defaultModelKey, keyPattern } from "./model-options";
import { ModelSettingsFields } from "./model-settings";
import { ModelPricing } from "./model-pricing";
import styles from "./models.module.css";

/** The model this draft points at, and the two fields that address it. */
export function ModelSelection({
  model,
  onChange,
  showAddress = true,
  compact = false,
}: {
  model: ModelDraft;
  showAddress?: boolean;
  compact?: boolean;
  onChange?: () => void;
}) {
  const { t } = useTranslation();
  const { draft, selectedEntry } = model;
  return (
    <div className={sharedStyles.stack}>
      <div className={compact ? styles.creationSource : styles.chosenRow}>
        {compact ? (
          <span className={styles.providerSummary}>
            <span>{t("Provider")}</span>
            <strong>{model.selectedProvider?.name}</strong>
            {!draft.catalog_ref && !showAddress && draft.model_name && (
              <small>{draft.model_name}</small>
            )}
          </span>
        ) : (
          <>
            <IconTile size={36} tone="elevated">
              <ModelIcon
                upstream={draft.model_name}
                catalogRef={draft.catalog_ref}
                provider={providerBrand(model.selectedProvider)}
                size={20}
              />
            </IconTile>
            {/* The model, then the provider serving it; the ID field below is never repeated. */}
            <span className={styles.chosenCopy}>
              <strong>
                {selectedEntry?.name ||
                  (!showAddress && draft.model_name) ||
                  t("Custom model")}
              </strong>
              {model.selectedProvider && (
                <small>{model.selectedProvider.name}</small>
              )}
            </span>
          </>
        )}
        <span className={styles.chosenActions}>
          {onChange && (
            <Button type="button" variant="ghost" size="sm" onClick={onChange}>
              {t(compact ? "Choose a model" : "Change")}
            </Button>
          )}
        </span>
      </div>
      {model.selectedProvider?.enabled === false && (
        <Alert variant="warning">
          <WarningIcon aria-hidden="true" />
          <AlertTitle>{t("Provider disabled")}</AlertTitle>
          <AlertDescription>
            {t("Agents can't use this model until {{provider}} is enabled.", {
              provider: model.selectedProvider.name,
            })}
          </AlertDescription>
        </Alert>
      )}
      {showAddress && <ModelAddress model={model} />}
    </div>
  );
}

function ModelAddress({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  const hintId = useId();
  const { draft, selectedEntry, definition, channels } = model;
  const compatible =
    !!selectedEntry && !channels.includes(selectedEntry.ref.provider);
  const multipleApis = (definition?.model_apis?.length ?? 0) > 1;
  return (
    <div className={styles.address}>
      <div className={multipleApis ? sharedStyles.twoColumns : undefined}>
        <FormField label={t("Upstream model")}>
          <Input
            required
            maxLength={256}
            aria-describedby={hintId}
            value={draft.model_name}
            onChange={(event) => model.change("model_name", event.target.value)}
          />
        </FormField>
        {multipleApis && (
          <ChoiceField
            label={t("API")}
            value={model.callingApi}
            onValueChange={(value) => model.change("model_api", value)}
            options={
              definition?.model_apis?.map((value) => ({
                value,
                label: definition.model_api_labels?.[value] ?? value,
              })) ?? []
            }
          />
        )}
      </div>
      {/* Spans both columns, so the hint never wraps in a half-width cell. */}
      <p id={hintId} className={styles.addressHint}>
        {t(
          compatible
            ? "Requires an OpenAI-compatible endpoint serving this model. Enter its upstream model ID."
            : "Sent to the provider. Change this for gateway aliases or deployment IDs.",
        )}
      </p>
    </div>
  );
}

/** Creating needs only a name; the same detailed controls remain available on demand. */
export function ModelFields({
  model,
  compact = false,
  onChangeModel,
}: {
  model: ModelDraft;
  compact?: boolean;
  onChangeModel?: () => void;
}) {
  const { t } = useTranslation();
  const { draft, selectedEntry } = model;
  const name = (
    <FormField label={t("Name")}>
      <Input
        required
        maxLength={128}
        value={draft.name}
        onChange={(event) => model.change("name", event.target.value)}
      />
    </FormField>
  );
  const details = (
    <div className={styles.modelDetails}>
      <FormSection title={t("Basic information")}>
        <div
          className={compact ? styles.identityFields : styles.editorIdentity}
        >
          {/* A saved model's key is fixed and already in the dialog title. */}
          {compact ? <ModelKeyField model={model} /> : name}
          <FormField label={t("Description")}>
            <Input
              maxLength={2048}
              value={draft.description}
              placeholder={t("Optional")}
              onChange={(event) =>
                model.change("description", event.target.value)
              }
            />
          </FormField>
        </div>
      </FormSection>
      {(!compact || !model.manual) && (
        <FormSection title={t("Model connection")}>
          {onChangeModel ? (
            <ModelSelection model={model} onChange={onChangeModel} />
          ) : (
            <ModelAddress model={model} />
          )}
        </FormSection>
      )}
      <ModelInformation
        value={draft.characteristics}
        onChange={(value) => model.change("characteristics", value)}
      />
      <ModelPricing
        value={draft.pricing}
        onChange={(pricing) => model.change("pricing", pricing)}
      />
      {selectedEntry?.pricing_warning && (
        <p className={styles.stepNote}>{t(selectedEntry.pricing_warning)}</p>
      )}
      {selectedEntry &&
        draft.pricing &&
        (model.customEndpoint ||
          !model.channels.includes(selectedEntry.ref.provider)) && (
          <p className={styles.stepNote}>
            {t(
              "Catalog prices are references from the model provider. Your gateway may charge differently.",
            )}
          </p>
        )}
    </div>
  );
  return (
    <>
      {compact ? (
        <>
          {name}
          <ModelCapabilitySummary value={draft.characteristics} />
        </>
      ) : (
        details
      )}
      <DisclosureSection
        className={styles.advancedSection}
        title={t("Advanced")}
        summary={
          model.settingsJson.trim() !== "{}" ||
          model.imageInput.policy !== undefined ||
          JSON.stringify(model.imageInput) !== JSON.stringify(imageInputDraft())
            ? t("Configured")
            : undefined
        }
        open={model.settingsExpanded}
        onOpenChange={model.setSettingsExpanded}
      >
        <div className={styles.modelDetails}>
          {compact && details}
          <div className={styles.advancedGroup}>
            <ModelSettingsFields
              value={model.settingsJson}
              onChange={model.setSettingsJson}
              schema={model.settingsSchema}
              modelApi={model.callingApi}
            />
          </div>
          <div className={styles.advancedGroup}>
            <ImageInputFields
              value={model.imageInput}
              onChange={model.setImageInput}
            />
          </div>
        </div>
      </DisclosureSection>
    </>
  );
}

/** The key agents name the model by; the Service derives one when left empty. */
function ModelKeyField({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  const derived = defaultModelKey(
    model.selectedProvider?.type,
    model.draft.model_name,
  );
  return (
    <FormField
      label={t("Model key")}
      description={t(
        "How agents refer to this model. Lowercase letters, numbers, hyphens and dots; it cannot change later.",
      )}
    >
      <Input
        required={!derived}
        maxLength={128}
        pattern={keyPattern}
        placeholder={derived}
        value={model.draft.key}
        onChange={(event) => model.change("key", event.target.value)}
      />
    </FormField>
  );
}

/** Whether agents can select the model. */
export function ModelStatus({ model }: { model: ModelDraft }) {
  return (
    <EnabledField
      disabled={model.save.isPending}
      checked={model.draft.enabled}
      onCheckedChange={(value) => model.change("enabled", value)}
    />
  );
}
