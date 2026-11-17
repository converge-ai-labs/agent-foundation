import { BrandIcon, Button, SearchPicker } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useWebProviders } from "../transport/context";
import type { Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import {
  SettingsRow,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import { SchemaForm } from "../forms/schema-form";
import { resolveField, type FormSchema, type Json } from "../forms/schema";
import {
  CredentialSourceField,
  type CredentialSource,
} from "./credential-source";
import { readDocument, template, updateDocument } from "./documents";
import styles from "./web-providers.module.css";

type WebProviderType = Schema<"WebProviderType">;

/** Which Agent operations a provider can serve, shown wherever it is chosen. */
export function OperationTags({
  operations,
}: {
  operations: readonly ("search" | "scrape")[];
}) {
  const { t } = useTranslation();
  return (
    <span className={styles.operations}>
      {operations.map((operation) => (
        <span key={operation} className={styles.operation}>
          {t(operation === "search" ? "Search" : "Scrape")}
        </span>
      ))}
    </span>
  );
}

export function credentialFields(type: WebProviderType) {
  const schema = type.credential_schema as FormSchema | null;
  return Object.entries(schema?.properties ?? {}).map(([name, field]) => ({
    name,
    title: resolveField(field, schema!).title ?? name,
    required: !!schema?.required?.includes(name),
  }));
}

/** One credential field in the add dialog; secrets stay in memory only. */
export type KeyChoice = {
  source: "none" | "new" | "saved" | "env";
  secret: string;
  env: string;
  /** Pre-allocated for a pasted key, or the chosen saved key. */
  reference: string;
};
export type AccountDraft = {
  id: string;
  type: WebProviderType;
  name: string;
  configuration: Record<string, Json>;
  keys: Record<string, KeyChoice>;
  /** Allocated once so a retried save overwrites the same stored keys. */
  references: Record<string, string>;
};

export function accountDraft(type: WebProviderType): AccountDraft {
  const fields = credentialFields(type);
  const references = Object.fromEntries(
    fields.map((field) => [
      field.name,
      `key-${crypto.randomUUID().replaceAll("-", "")}`,
    ]),
  );
  return {
    id: `web-provider-${crypto.randomUUID().slice(0, 8)}`,
    type,
    name: type.display_name,
    configuration: {},
    references,
    keys: Object.fromEntries(
      fields.map((field) => [
        field.name,
        {
          source: field.required ? "new" : "none",
          secret: "",
          env: "",
          reference: references[field.name],
        },
      ]),
    ),
  };
}

/** The account file a draft would save: credential sources, never secrets. */
export function accountSource(draft: AccountDraft) {
  const mode = credentialMode(draft.type, draft.configuration);
  const fields = credentialFields(draft.type);
  const provided: Record<string, CredentialSource> = {};
  for (const { name } of fields) {
    const choice = draft.keys[name];
    if (
      (choice.source === "new" && choice.secret.trim()) ||
      choice.source === "saved"
    )
      provided[name] = { credential_ref: choice.reference };
    else if (choice.source === "env" && choice.env.trim())
      provided[name] = { env: choice.env.trim() };
  }
  const credential =
    mode === "forbidden" ||
    (mode === "optional" && !Object.keys(provided).length)
      ? undefined
      : provided;
  const complete = fields.every(
    (field) => !field.required || provided[field.name],
  );
  let content = template("web_provider", draft.id);
  content = updateDocument(content, ["name"], draft.name.trim());
  content = updateDocument(content, ["type"], draft.type.type);
  content = updateDocument(content, ["configuration"], draft.configuration);
  content = updateDocument(content, ["credential"], credential);
  return {
    mode,
    content,
    ready: !!draft.name.trim() && (credential === undefined || complete),
  };
}

export function WebProviderIdentity({
  type,
  name,
}: {
  type: string;
  name: string;
}) {
  return (
    <span className={styles.identity}>
      <BrandIcon identity={type} alias={type} size={18} />
      <span>{name}</span>
    </span>
  );
}

export function WebProviderPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const catalog = useWebProviders();
  return (
    <>
      <SearchPicker
        label={t("Provider type")}
        value={value}
        placeholder={t("Choose a Web provider")}
        emptyMessage={t("No matching Web providers.")}
        groups={[
          {
            label: t("Web providers"),
            options: (catalog.data?.types ?? []).map((item) => ({
              value: item.type,
              label: item.display_name,
              icon: (
                <BrandIcon identity={item.type} alias={item.type} size={18} />
              ),
              description: item.operations
                .map((operation) =>
                  t(operation === "search" ? "Search" : "Scrape"),
                )
                .join(" · "),
            })),
          },
        ]}
        onValueChange={onChange}
      />
      <ErrorNotice error={catalog.error} retry={() => void catalog.refetch()} />
    </>
  );
}

export function credentialMode(
  type: Schema<"WebProviderType">,
  configuration: Record<string, Json>,
) {
  const schema = type.configuration_schema as FormSchema;
  const matches =
    type.authentication.cases?.filter((item) => {
      const value = Object.hasOwn(configuration, item.field)
        ? configuration[item.field]
        : resolveField(schema.properties?.[item.field] ?? {}, schema).default;
      return value === item.equals;
    }) ?? [];
  return matches[0]?.mode ?? type.authentication.mode ?? "required";
}

export function WebProviderFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const catalog = useWebProviders();
  const document = readDocument(source);
  const raw = document?.toJS();
  const type = catalog.data?.types.find((item) => item.type === raw?.type);
  const configuration = raw?.configuration ?? {};
  const credential: Record<string, CredentialSource> | undefined =
    raw?.credential;
  const set = (path: string[], value: unknown) =>
    onChange(updateDocument(source, path, value));
  const mode = type ? credentialMode(type, configuration) : "forbidden";
  const schema = type?.credential_schema as FormSchema | undefined;
  return (
    <>
      <SettingsSection title={t("Web provider")}>
        {type ? (
          <SettingsRow
            label={
              <WebProviderIdentity type={type.type} name={type.display_name} />
            }
          >
            <OperationTags operations={type.operations} />
          </SettingsRow>
        ) : (
          <WebProviderPicker
            value={raw?.type ?? ""}
            onChange={(value) => set(["type"], value)}
          />
        )}
        {type?.setup_url && (
          <div className={styles.actions}>
            <Button
              variant="link"
              render={
                <a href={type.setup_url} target="_blank" rel="noreferrer" />
              }
            >
              {type.setup_label ?? t("Open provider setup")}
            </Button>
          </div>
        )}
      </SettingsSection>
      {type &&
        Object.keys(type.configuration_schema.properties ?? {}).length > 0 && (
          <SettingsSection title={t("Provider configuration")}>
            <SchemaForm
              schema={type.configuration_schema as FormSchema}
              value={configuration}
              preserveOmitted
              onChange={(value) => set(["configuration"], value)}
              onPatch={(path, value) => set(["configuration", ...path], value)}
            />
          </SettingsSection>
        )}
      {schema && mode !== "forbidden" && (
        <SettingsSection
          title={t("Credentials")}
          description={t(
            "Saved separately from this account. Values are never included in configuration files.",
          )}
        >
          {mode === "optional" && (
            <SettingsToggle
              label={t("Use credentials")}
              description={t("This provider also supports anonymous access.")}
              checked={credential !== undefined}
              onCheckedChange={(checked) =>
                set(["credential"], checked ? {} : undefined)
              }
            />
          )}
          {(mode === "required" || credential !== undefined) &&
            Object.entries(schema.properties ?? {}).map(([name, field]) => {
              const resolved = resolveField(field, schema);
              const required = schema.required?.includes(name);
              return (
                <div key={name} className={styles.credential}>
                  {!required && (
                    <SettingsToggle
                      label={resolved.title ?? name}
                      checked={credential?.[name] !== undefined}
                      onCheckedChange={(checked) =>
                        set(
                          ["credential", name],
                          checked ? { env: "PROVIDER_CREDENTIAL" } : undefined,
                        )
                      }
                    />
                  )}
                  {(required || credential?.[name] !== undefined) && (
                    <CredentialSourceField
                      key={`${raw.type}/${name}`}
                      generic
                      label={resolved.title ?? name}
                      value={credential?.[name]}
                      onChange={(next) => set(["credential", name], next)}
                    />
                  )}
                </div>
              );
            })}
        </SettingsSection>
      )}
      {mode === "forbidden" && credential !== undefined && (
        <SettingsSection title={t("Credentials")}>
          <SettingsRow
            label={t("Credentials are not accepted")}
            description={t(
              "Remove the credential sources to use this provider configuration.",
            )}
          >
            <Button
              variant="outline"
              onClick={() => set(["credential"], undefined)}
            >
              {t("Remove credential sources")}
            </Button>
          </SettingsRow>
        </SettingsSection>
      )}
      <p className={styles.note}>
        {t(
          "Saving checks the account recipe, not network access. Credentials are resolved when an agent uses this account.",
        )}
      </p>
      <ErrorNotice error={catalog.error} />
    </>
  );
}
