import { useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrandIcon, Button, FormField, Input, ModalFrame } from "a13n-ui";
import { ArrowLeft, Plus } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import {
  useSources,
  useTransport,
  useWebProviders,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, SearchField } from "../shell/ui";
import { SettingsChoice, SettingsSection } from "../shell/settings";
import { SchemaForm } from "../forms/schema-form";
import type { FormSchema, Json } from "../forms/schema";
import { DraftLinks } from "./sources";
import {
  accountDraft,
  accountSource,
  credentialFields,
  OperationTags,
  WebProviderIdentity,
  type AccountDraft,
  type KeyChoice,
} from "./web-providers";
import layout from "./settings.module.css";
import styles from "../shell/workbench.module.css";
import webStyles from "./web-providers.module.css";

export function WebProvidersSection() {
  const { t } = useTranslation();
  const catalog = useWebProviders();
  const sources = useSources();
  const [adding, setAdding] = useState(0);
  const [search, setSearch] = useState("");
  const accounts = catalog.data?.accounts ?? [];
  const visible = accounts.filter((item) =>
    `${item.name} ${item.type} ${item.id}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <section className={layout.collection}>
      <PageHeader
        level={2}
        title={t("Web providers")}
        description={t(
          "Search and scrape services for agents. Choose them in each agent's Web options.",
        )}
        actions={
          <Button
            variant="outline"
            disabled={!catalog.data}
            onClick={() => setAdding((current) => current + 1)}
          >
            <Plus />
            {t("Add Web provider")}
          </Button>
        }
      />
      {accounts.length > 0 && (
        <SearchField
          label={t("Search Web accounts")}
          value={search}
          onChange={setSearch}
        />
      )}
      <ErrorNotice
        error={catalog.error || sources.error}
        retry={() => void catalog.refetch()}
      />
      <DraftLinks kinds={["web_provider"]} search={search} />
      <div className={styles.resourceList}>
        {visible.map((account) => {
          const path = sources.data?.sources.find(
            (item) =>
              item.resource_kind === "web_provider" &&
              item.resource_ids.includes(account.id),
          )?.relative_path;
          const definition = catalog.data?.types.find(
            (item) => item.type === account.type,
          );
          return (
            path && (
              <Link
                key={account.id}
                className={styles.resourceRow}
                to={`/settings/source?path=${encodeURIComponent(path)}`}
              >
                <div>
                  <strong>
                    <WebProviderIdentity
                      type={account.type}
                      name={account.name}
                    />
                  </strong>
                  <small>{definition?.display_name ?? account.type}</small>
                </div>
                <OperationTags operations={definition?.operations ?? []} />
              </Link>
            )
          );
        })}
      </div>
      {!catalog.isPending && !accounts.length && !catalog.error && (
        <p className={layout.emptySection}>
          {t(
            "No Web accounts yet. Local search and HTML scraping remain available without one.",
          )}
        </p>
      )}
      {!!accounts.length && !visible.length && (
        <p className={layout.emptySection}>{t("No matching Web accounts.")}</p>
      )}
      {adding > 0 && catalog.data && (
        <AddWebProviderDialog
          key={adding}
          types={catalog.data.types}
          onClose={() => setAdding(0)}
        />
      )}
    </section>
  );
}

/**
 * Choose a service, then connect it in one step. Pasted keys go to the key
 * store; the account file receives only their references.
 */
function AddWebProviderDialog({
  types,
  onClose,
}: {
  types: readonly Schema<"WebProviderType">[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const [draft, setDraft] = useState<AccountDraft>();
  const [query, setQuery] = useState("");
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  const account = draft && accountSource(draft);
  const save = useMutation({
    mutationFn: async ({
      draft,
      content,
    }: {
      draft: AccountDraft;
      content: string;
    }) => {
      const path = `web_providers/${draft.id}.yaml`;
      // Check the recipe first so an invalid account stores no new key.
      await result(
        client.POST("/api/configuration/validate", {
          params: { query: { path } },
          body: { content },
        }),
      );
      for (const choice of Object.values(draft.keys))
        if (choice.source === "new" && choice.secret.trim())
          await result(
            client.PUT("/api/auth/keys", {
              body: { credential_ref: choice.reference, key: choice.secret },
            }),
          );
      await result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          body: { content },
        }),
      );
    },
    onSuccess: async () => {
      await queries.invalidateQueries();
      onClose();
    },
    onError: () => void queries.invalidateQueries({ queryKey: ["keys"] }),
  });
  const term = query.trim().toLowerCase();
  const submit = () => {
    if (draft && account?.ready && !save.isPending)
      save.mutate({ draft, content: account.content });
  };
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
      disablePointerDismissal={!!draft}
      closeLabel={t("Close")}
      title={
        draft ? (
          <WebProviderIdentity
            type={draft.type.type}
            name={t("Connect {{provider}}", {
              provider: draft.type.display_name,
            })}
          />
        ) : (
          t("Add Web provider")
        )
      }
      description={draft ? undefined : t("Choose a search or scrape service.")}
      footer={
        draft && (
          <>
            <Button
              variant="outline"
              disabled={save.isPending}
              onClick={onClose}
            >
              {t("Cancel")}
            </Button>
            <Button
              disabled={!account?.ready}
              loading={save.isPending}
              onClick={submit}
            >
              {t("Add")}
            </Button>
          </>
        )
      }
    >
      {draft ? (
        <ConnectFields
          draft={draft}
          keys={keys.data ?? []}
          disabled={save.isPending}
          onBack={() => setDraft(undefined)}
          onChange={setDraft}
          onSubmit={submit}
        >
          <ErrorNotice error={save.error || keys.error} />
        </ConnectFields>
      ) : (
        <div className={webStyles.chooser}>
          {types.length > 6 && (
            <Input
              type="search"
              autoFocus
              aria-label={t("Search providers…")}
              placeholder={t("Search providers…")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          )}
          <div className={webStyles.catalog}>
            {types
              .filter((item) =>
                `${item.display_name} ${item.type}`
                  .toLowerCase()
                  .includes(term),
              )
              .map((item) => (
                <button
                  key={item.type}
                  type="button"
                  className={webStyles.catalogItem}
                  onClick={() => setDraft(accountDraft(item))}
                >
                  <BrandIcon identity={item.type} alias={item.type} size={20} />
                  <span>{item.display_name}</span>
                  <OperationTags operations={item.operations} />
                </button>
              ))}
          </div>
        </div>
      )}
    </ModalFrame>
  );
}

function ConnectFields({
  draft,
  keys,
  disabled,
  onBack,
  onChange,
  onSubmit,
  children,
}: {
  draft: AccountDraft;
  keys: readonly Schema<"ApiKeyStatus">[];
  disabled: boolean;
  onBack: () => void;
  onChange: (draft: AccountDraft) => void;
  onSubmit: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const { type } = draft;
  const { mode } = accountSource(draft);
  const fields = credentialFields(type);
  const schema = type.configuration_schema as FormSchema;
  const configurable = Object.keys(schema.properties ?? {}).length > 0;
  const configurationRequired = !!schema.required?.length;
  const setKey = (field: string, next: Partial<KeyChoice>) =>
    onChange({
      ...draft,
      keys: { ...draft.keys, [field]: { ...draft.keys[field], ...next } },
    });
  const configuration = (
    <SchemaForm
      schema={schema}
      value={draft.configuration}
      preserveOmitted
      onChange={(value) =>
        onChange({
          ...draft,
          configuration: (value ?? {}) as Record<string, Json>,
        })
      }
    />
  );
  // The resting form shows required keys being pasted; every other credential
  // or configuration choice waits under More options.
  const keyInput = (field: (typeof fields)[number]) => {
    const choice = draft.keys[field.name];
    if (choice.source === "new")
      return (
        <FormField
          key={field.name}
          label={t(field.title)}
          description={
            mode === "optional"
              ? t("Optional. Leave empty for anonymous access.")
              : undefined
          }
        >
          <Input
            type="password"
            autoComplete="new-password"
            value={choice.secret}
            onChange={(event) =>
              setKey(field.name, { secret: event.target.value })
            }
          />
        </FormField>
      );
    if (choice.source === "env")
      return (
        <FormField
          key={field.name}
          label={t("{{field}} environment variable", { field: t(field.title) })}
          description={t("Read from the server environment.")}
        >
          <Input
            value={choice.env}
            autoComplete="off"
            onChange={(event) =>
              setKey(field.name, { env: event.target.value })
            }
          />
        </FormField>
      );
    return null;
  };
  const advancedKeys = mode !== "forbidden" && fields.length > 0;
  return (
    <form
      className={webStyles.connect}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <fieldset disabled={disabled} className={webStyles.connect}>
        <div className={webStyles.connectHeader}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={webStyles.back}
            onClick={onBack}
          >
            <ArrowLeft />
            {t("All providers")}
          </Button>
          <OperationTags operations={type.operations} />
        </div>
        <FormField label={t("Name")}>
          <Input
            value={draft.name}
            autoComplete="off"
            onChange={(event) =>
              onChange({ ...draft, name: event.target.value })
            }
          />
        </FormField>
        {mode === "forbidden" ? (
          <p className={webStyles.hint}>{t("No API key needed.")}</p>
        ) : (
          fields
            .filter(
              (field) =>
                field.required || draft.keys[field.name].source !== "none",
            )
            .map(keyInput)
        )}
        {type.setup_url && (
          <Button
            variant="link"
            className={webStyles.setupLink}
            render={
              <a href={type.setup_url} target="_blank" rel="noreferrer" />
            }
          >
            {type.setup_label ?? t("Open provider setup")}
          </Button>
        )}
        {configurable && configurationRequired && configuration}
        {mode !== "forbidden" && (
          <p className={webStyles.hint}>
            {t(
              "Keys are saved separately and can be reused in Accounts & API keys. Nothing is sent to the provider until an agent uses this account.",
            )}
          </p>
        )}
        {(advancedKeys || (configurable && !configurationRequired)) && (
          <details className={layout.groupDisclosure}>
            <summary>
              <span>{t("More options")}</span>
            </summary>
            <div className={webStyles.connect}>
              {advancedKeys && (
                <SettingsSection
                  title={t("Credential sources")}
                  description={t(
                    "Use a saved key or a server environment variable instead of pasting one.",
                  )}
                >
                  {fields.map((field) => (
                    <SettingsChoice
                      key={field.name}
                      label={t(field.title)}
                      value={
                        draft.keys[field.name].source === "saved"
                          ? draft.keys[field.name].reference
                          : draft.keys[field.name].source
                      }
                      options={[
                        ...(field.required && mode === "required"
                          ? []
                          : [{ value: "none", label: t("Not used") }]),
                        { value: "new", label: t("Paste a key") },
                        ...keys.map((key) => ({
                          value: key.credential_ref,
                          label: key.credential_ref,
                        })),
                        {
                          value: "env",
                          label: t("Server environment variable"),
                        },
                      ]}
                      onValueChange={(value) =>
                        setKey(
                          field.name,
                          ["none", "new", "env"].includes(value)
                            ? {
                                source: value as KeyChoice["source"],
                                secret: "",
                                reference: draft.references[field.name],
                              }
                            : { source: "saved", secret: "", reference: value },
                        )
                      }
                    />
                  ))}
                </SettingsSection>
              )}
              {configurable && !configurationRequired && (
                <SettingsSection title={t("Provider configuration")}>
                  {configuration}
                </SettingsSection>
              )}
            </div>
          </details>
        )}
      </fieldset>
      {children}
    </form>
  );
}
