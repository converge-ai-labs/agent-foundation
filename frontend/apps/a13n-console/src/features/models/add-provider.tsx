import { FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { BrandTitle, CatalogStep } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, ProviderKeyLink, SchemaFields } from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import {
  AddProviderDialog,
  ProviderCatalog,
  credentialHint,
  providerKeyLink,
} from "../providers";
import { type ModelScope } from "./api";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { useModelProviderDefinitions } from "./provider-definitions";
import { credentialFieldFor, useProviderDraft } from "./provider-draft";

type Definition = Schema["ModelProviderMetadata"];

/** Familiar services first; the rest keep the catalog order. */
const featured = [
  "openai",
  "anthropic",
  "google_gemini",
  "google_vertex",
  "aws_bedrock",
  "azure_openai",
  "openrouter",
  "deepseek",
  "moonshot",
  "zhipu",
  "minimax",
  "alibaba_model_studio",
  "ollama",
];
function hint(definition: Definition) {
  if (definition.authentication.mode === "forbidden") return "Local endpoint";
  return credentialHint(definition.credential_schema);
}

/** What the model catalog looks like wherever it appears. */
export const modelCatalog = {
  featured,
  hint,
  note: "Using a gateway or an OpenAI-compatible endpoint? Choose OpenAI and set its base URL under Advanced settings.",
};

export function connectStepTitle(
  definition: Definition,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return (
    <BrandTitle mark={<ProviderIcon type={definition.type} />}>
      {t("Connect {{provider}}", { provider: definition.display_name })}
    </BrandTitle>
  );
}

export function connectStepDescription(
  definition: Definition,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return t(
    definition.authentication.mode === "forbidden"
      ? "Point the console at the endpoint that serves your models."
      : credentialFieldFor(definition).description,
    { provider: definition.display_name },
  );
}

/** The model catalog, prefilled with the model hints and ordering. */
export function ModelProviderCatalog({
  definitions,
  onChoose,
}: {
  definitions: Definition[];
  onChoose: (type: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <ProviderCatalog
      definitions={definitions}
      featured={featured}
      hint={hint}
      note={t(modelCatalog.note)}
      onChoose={onChoose}
    />
  );
}

/** Catalog-first provider creation, used for both workspace and organization. */
export function AddProvider({ scope }: { scope: ModelScope }) {
  const [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const definitions = useModelProviderDefinitions();
  return (
    <AddProviderCatalogDialog
      key={generation}
      scope={scope}
      definitions={definitions.data?.items}
      error={definitions.error}
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setGeneration((current) => current + 1);
      }}
    />
  );
}

function AddProviderCatalogDialog({
  scope,
  definitions,
  error,
  open,
  onOpenChange,
}: {
  scope: ModelScope;
  definitions?: Definition[];
  error: unknown;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const draft = useProviderDraft({
    scope,
    definitions: definitions ?? [],
    initialType: "",
    close: () => onOpenChange(false),
  });
  return (
    <AddProviderDialog<Definition>
      definitions={definitions}
      error={error}
      open={open}
      onOpenChange={onOpenChange}
      description={t("Choose the service that hosts your models.")}
      featured={featured}
      hint={hint}
      note={t(modelCatalog.note)}
      onChoose={draft.chooseType}
      connectTitle={(definition) => connectStepTitle(definition, t)}
      connectDescription={(definition) => connectStepDescription(definition, t)}
    >
      {(definition, back) => (
        <ProviderConnectForm
          draft={draft}
          definition={definition}
          onBack={back}
        />
      )}
    </AddProviderDialog>
  );
}

/** Credential, name, and advanced settings for the chosen provider. */
export function ProviderConnectForm({
  draft,
  definition,
  onBack,
  backLabel,
  submitLabel,
}: {
  draft: ReturnType<typeof useProviderDraft>;
  definition: Definition;
  onBack?: () => void;
  backLabel?: string;
  submitLabel?: string;
}) {
  const { t } = useTranslation();
  const { type, save } = draft;
  const keyLink = providerKeyLink(definition);
  return (
    <CatalogStep backLabel={backLabel ?? t("All providers")} onBack={onBack}>
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        {draft.section.mode !== "forbidden" && (
          <SchemaFields
            secret
            autoFocus
            labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
            schema={draft.section.schema}
            value={draft.section.credential}
            onChange={draft.section.setCredential}
          />
        )}
        <SchemaFields
          schema={ordinaryConfigurationSchema(definition.configuration_schema)}
          value={draft.configuration}
          onChange={draft.setConfiguration}
        />
        <FormField
          label={t("Name")}
          description={t("How this provider is listed across the console.")}
        >
          <Input
            required
            value={draft.name}
            maxLength={128}
            onChange={(event) => draft.setName(event.target.value)}
          />
        </FormField>
        <ProviderConnection
          type={type}
          schema={definition.configuration_schema}
          configuration={draft.configuration}
          onChange={draft.setConfiguration}
          headers={draft.headers}
          onHeadersChange={draft.setHeaders}
          open={draft.advancedOpen}
          onOpenChange={draft.setAdvancedOpen}
          onBaseUrlChange={draft.changeBaseUrl}
          onSuggestedApi={draft.setSuggestedApi}
        />
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          label={submitLabel ?? t("Add provider")}
          onCancel={draft.close}
        />
      </form>
    </CatalogStep>
  );
}
