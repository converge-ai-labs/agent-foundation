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
import { readDocument, updateDocument } from "./documents";
import styles from "./web-providers.module.css";

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
            label={t("Provider type")}
            description={type.operations
              .map((operation) =>
                t(operation === "search" ? "Search" : "Scrape"),
              )
              .join(" · ")}
          >
            <WebProviderIdentity type={type.type} name={type.display_name} />
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
