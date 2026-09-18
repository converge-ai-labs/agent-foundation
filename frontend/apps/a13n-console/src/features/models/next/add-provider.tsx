import { ArrowLeftIcon, PlusIcon } from "@phosphor-icons/react";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { FormActions } from "../../../shared/form";
import { ProviderIcon } from "../../../shared/provider-icon";
import { ProviderKeyLink } from "../../../shared/provider-key-link";
import { SchemaFields } from "../../../shared/schema-fields";
import { type ModelScope } from "../api";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "../provider-connection";
import { requiresProviderCredential } from "../provider-credentials";
import { useModelProviderDefinitions } from "../provider-definitions";
import { credentialFieldFor, useProviderDraft } from "../provider-draft";
import { providerKeyUrls } from "../provider-key-urls";
import styles from "./next.module.css";

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

export function AddProviderNext({ scope }: { scope: ModelScope }) {
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
      title={
        chosen ? (
          <span className={styles.brandTitle}>
            <span className={styles.brandMark}>
              <ProviderIcon type={chosen.type} />
            </span>
            {t("Connect {{provider}}", { provider: chosen.display_name })}
          </span>
        ) : (
          t("Add provider")
        )
      }
      description={
        chosen
          ? t(
              chosen.credential_schema.type === "null"
                ? "Point the console at the endpoint that serves your models."
                : credentialFieldFor(chosen).description,
            )
          : t("Choose the service that hosts your models.")
      }
    >
      {open &&
        (error ? (
          <ErrorNotice error={error} />
        ) : !definitions ? (
          <Loading variant="form" rows={3} />
        ) : chosen ? (
          <ConnectForm draft={draft} definition={chosen} />
        ) : (
          <ProviderCatalog
            definitions={definitions}
            onChoose={draft.chooseType}
          />
        ))}
    </ModalFrame>
  );
}

function ProviderCatalog({
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
    <div className={styles.catalog}>
      <Input
        type="search"
        autoFocus
        aria-label={t("Search providers…")}
        placeholder={t("Search providers…")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {visible.length ? (
        <div className={styles.tiles}>
          {visible.map((definition) => (
            <button
              key={definition.type}
              type="button"
              className={styles.tile}
              onClick={() => onChoose(definition.type)}
            >
              <span className={styles.tileIcon}>
                <ProviderIcon type={definition.type} />
              </span>
              <span className={styles.tileCopy}>
                <strong>{definition.display_name}</strong>
                <small>{t(hint(definition))}</small>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.empty}>{t("No matching providers")}</p>
      )}
      <p className={styles.catalogFoot}>
        {t(
          "Using a gateway or an OpenAI-compatible endpoint? Choose OpenAI and set its base URL under Advanced settings.",
        )}
      </p>
    </div>
  );
}

function ConnectForm({
  draft,
  definition,
}: {
  draft: ReturnType<typeof useProviderDraft>;
  definition: Definition;
}) {
  const { t } = useTranslation();
  const { type, save } = draft;
  const keyLink = providerKeyUrls[type];
  return (
    <form
      className={styles.connect}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className={styles.back}
        onClick={() => draft.chooseType("")}
      >
        <ArrowLeftIcon aria-hidden="true" />
        {t("All providers")}
      </Button>
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
        label={t("Add provider")}
        onCancel={draft.close}
      />
    </form>
  );
}
