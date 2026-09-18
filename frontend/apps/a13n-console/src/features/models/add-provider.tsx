import { PlusIcon } from "@phosphor-icons/react";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import {
  BrandTitle,
  CatalogStep,
  CatalogTile,
  CatalogTiles,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, ProviderKeyLink, SchemaFields } from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import { type ModelScope } from "./api";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { requiresProviderCredential } from "./provider-credentials";
import { useModelProviderDefinitions } from "./provider-definitions";
import { credentialFieldFor, useProviderDraft } from "./provider-draft";
import { providerKeyUrls } from "./provider-key-urls";

type Definition = Schema["ModelProviderDefinition"];

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
const credentialHints: Record<string, string> = {
  api_key: "API key",
  google_service_account_json: "Service account",
  aws_credentials_json: "Access keys",
};

function hint(definition: Definition) {
  if (definition.credential_schema.type === "null") return "Local endpoint";
  return (
    credentialHints[
      String(definition.credential_schema["x-a13n-credential-format"])
    ] ?? "Secret"
  );
}

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
  t: (key: string) => string,
) {
  return t(
    definition.credential_schema.type === "null"
      ? "Point the console at the endpoint that serves your models."
      : credentialFieldFor(definition).description,
  );
}

/** Catalog-first provider creation, used for both workspace and organization. */
export function AddProvider({ scope }: { scope: ModelScope }) {
  const [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const definitions = useModelProviderDefinitions();
  return (
    <AddProviderDialog
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

function AddProviderDialog({
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
  const chosen = draft.definition;
  return (
    <ModalFrame
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      placement="top"
      closeLabel={t("Close")}
      trigger={
        <Button type="button">
          <PlusIcon aria-hidden="true" />
          {t("Add provider")}
        </Button>
      }
      title={chosen ? connectStepTitle(chosen, t) : t("Add provider")}
      description={
        chosen
          ? connectStepDescription(chosen, t)
          : t("Choose the service that hosts your models.")
      }
    >
      {open &&
        (error ? (
          <ErrorNotice error={error} />
        ) : !definitions ? (
          <Loading variant="form" rows={3} />
        ) : chosen ? (
          <ProviderConnectForm
            draft={draft}
            definition={chosen}
            onBack={() => draft.chooseType("")}
          />
        ) : (
          <ProviderCatalog
            definitions={definitions}
            onChoose={draft.chooseType}
          />
        ))}
    </ModalFrame>
  );
}

/** Tile grid of model services, shared by the dialog and the model form. */
export function ProviderCatalog({
  definitions,
  onChoose,
}: {
  definitions: Definition[];
  onChoose: (type: string) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const term = query.trim().toLocaleLowerCase();
  const rank = (definition: Definition) => {
    const index = featured.indexOf(definition.type);
    return index === -1 ? featured.length : index;
  };
  const visible = definitions
    .filter(
      (definition) =>
        !term ||
        `${definition.display_name} ${definition.type}`
          .toLocaleLowerCase()
          .includes(term),
    )
    .sort((a, b) => rank(a) - rank(b));
  return (
    <CatalogTiles
      search={
        <Input
          type="search"
          autoFocus
          aria-label={t("Search providers…")}
          placeholder={t("Search providers…")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      }
      empty={t("No matching providers")}
      note={t(
        "Using a gateway or an OpenAI-compatible endpoint? Choose OpenAI and set its base URL under Advanced settings.",
      )}
    >
      {visible.length
        ? visible.map((definition) => (
            <CatalogTile
              key={definition.type}
              icon={<ProviderIcon type={definition.type} />}
              name={definition.display_name}
              detail={t(hint(definition))}
              onClick={() => onChoose(definition.type)}
            />
          ))
        : undefined}
    </CatalogTiles>
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
  const keyLink = providerKeyUrls[type];
  return (
    <CatalogStep backLabel={backLabel ?? t("All providers")} onBack={onBack}>
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        {requiresProviderCredential(type, draft.configuration, definition) && (
          <FormField
            label={t(draft.credentialField.label)}
            labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
          >
            <Input
              type="password"
              autoFocus
              autoComplete="new-password"
              name="provider-api-key"
              value={draft.credential}
              onChange={(event) => draft.setCredential(event.target.value)}
            />
          </FormField>
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
          onAuthChange={draft.changeAuth}
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
