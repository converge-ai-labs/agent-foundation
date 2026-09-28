import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import {
  CatalogStep,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  jsonObject,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { useCredentialSection } from "../../shared/use-credential-section";
import { CallbackUrlField } from "../connections/callback-url";
import styles from "../../shared/shared.module.css";
import {
  AddProviderDialog,
  ConnectionTest,
  CredentialRow,
  EditProviderDialog,
  ProviderConnectFields,
  ProviderEditor,
  ProviderFacts,
  ProviderGroup,
  ProviderName,
  ProviderReadOnly,
  ProviderTable,
  credentialDescription,
  credentialHint,
  credentialLabel,
  providerKeyLink,
  providerStyles,
  providerTestResult,
} from "../providers";
import { connectorApi } from "./api";

type Definition = Schema["ProviderType"];

function useConnectorDefinitions(enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: ["connector-provider-types"],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: "connector" } },
          signal,
        })
        .then(data),
  });
}

export function ConnectorProviders() {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    page = useCursor();
  const rows = useResourceRows<Schema["Provider"]>();
  const query = useQuery({
    queryKey: ["connector-providers", workspace.id, "list", page.cursor],
    queryFn: ({ signal }) =>
      connectorApi(client, workspace.id).providers(signal, page.cursor),
  });
  const manage = can("write");
  return (
    <>
      {rows.selected && (
        <EditConnectorProvider
          key={rows.selected.id}
          providerId={rows.selected.id}
          readOnly={!manage}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="connectors"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={manage ? <AddConnectorProvider /> : undefined}
        canActivateRow={(item) => manage || item.enabled}
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          definition: item.type,
          type: item.type,
          credentials: item.credential_configured
            ? "configured"
            : "not_configured",
          state: item.enabled ? "enabled" : "disabled",
        })}
      />
    </>
  );
}

/** Catalog-first creation: choose the broker, then connect it. */
function AddConnectorProvider() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const definitions = useConnectorDefinitions();
  // Closing discards the step and the draft, whoever asked for it.
  function change(value: boolean) {
    setOpen(value);
    if (!value) setGeneration((current) => current + 1);
  }
  return (
    <AddProviderDialog<Definition>
      key={generation}
      definitions={definitions.data?.items}
      error={definitions.error}
      open={open}
      onOpenChange={change}
      description={t("Choose the service that brokers your agents' accounts.")}
      hint={(definition) => credentialHint(definition.credential_schema)}
      connectDescription={(definition) =>
        t(credentialDescription(definition.credential_schema), {
          provider: definition.display_name,
        })
      }
    >
      {(definition, back) => (
        <CatalogStep backLabel={t("All providers")} onBack={back}>
          <ProviderForm
            definition={definition}
            close={() => change(false)}
            reload={async () => {}}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

function EditConnectorProvider({
  providerId,
  controlledOpen,
  onClose,
  finalFocus,
  readOnly = false,
}: ResourceEditorControl & {
  providerId: string;
  readOnly?: boolean;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    [generation, setGeneration] = useState(0);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const definitions = useConnectorDefinitions(state.open);
  const resource = useQuery({
    queryKey: ["connector-providers", workspace.id, "detail", providerId],
    enabled: state.open,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/connector-providers/{provider_id}", {
          params: { path: { provider_id: providerId } },
          signal,
        })
        .then(data),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === resource.data?.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={resource.data?.name}
      id={providerId}
      type={resource.data?.type}
      definition={definition?.display_name}
      readOnly={readOnly}
      loading={definitions.isPending || resource.isPending}
      error={definitions.error ?? resource.error}
    >
      {resource.data &&
        (readOnly ? (
          <ProviderReadOnly
            enabled={resource.data.enabled}
            credentials={
              resource.data.credential_configured
                ? "configured"
                : "not_configured"
            }
            configuration={resource.data.config}
            schema={definition?.configuration_schema}
            only={["endpoint"]}
            onClose={() => state.setOpen(false)}
          />
        ) : (
          <ProviderForm
            key={generation}
            initial={resource.data}
            definition={definition}
            close={() => state.setOpen(false)}
            reload={async () => {
              await resource.refetch();
              setGeneration((value) => value + 1);
            }}
          />
        ))}
    </EditProviderDialog>
  );
}

/** Where the broker's own OAuth callback has to point for account authorization. */
function OAuthCallbackSetup() {
  const { t } = useTranslation();
  return (
    <DisclosureSection title={t("OAuth callback setup")}>
      <CallbackUrlField
        description={t(
          "For OAuth, open that project's Settings → OAuth user verification and set its callback URL to this exact address.",
        )}
      />
      <p className={styles.muted}>
        {t(
          "Composio managed apps work without your own OAuth client. To use a custom app or different scopes, create an auth config in Composio Dashboard. Account credentials are collected on Composio's hosted page.",
        )}
      </p>
      <ProviderKeyLink
        href="https://docs.composio.dev/docs/tools-direct/authenticating-tools"
        label="Open setup guide"
      />
    </DisclosureSection>
  );
}

function ProviderForm({
  initial,
  definition,
  close,
  reload,
}: {
  initial?: Schema["Provider"];
  definition?: Definition;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    { workspace } = useWorkspace(),
    [basis] = useState(initial),
    [name, setName] = useState(initial?.name ?? definition?.display_name ?? ""),
    [enabled, setEnabled] = useState(initial?.enabled ?? false),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.config ?? {},
    ),
    [advancedOpen, setAdvancedOpen] = useState(false);
  const type = initial?.type ?? definition?.type ?? "";
  const section = useCredentialSection(definition, configuration, basis);
  function done() {
    void cache.invalidateQueries({ queryKey: ["connector-providers"] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      const secret = section.payload();
      if (basis) {
        if (secret) {
          if (!definition) throw new Error(t("Provider unavailable."));
          validateSettings(section.schema, secret);
        }
        return client
          .workspace(workspace.id)
          .PATCH("/api/v1/connector-providers/{provider_id}", {
            params: { path: { provider_id: basis.id } },
            headers: ifMatch(rowTag(basis)),
            body: {
              name,
              enabled,
              ...(secret === undefined
                ? {}
                : {
                    credential:
                      secret === null
                        ? null
                        : jsonObject(JSON.stringify(secret)),
                  }),
            },
          })
          .then(data);
      }
      if (!definition) throw new Error(t("Select a provider type."));
      const config = withSchemaValues(
        definition.configuration_schema,
        configuration,
      );
      validateSettings(definition.configuration_schema, config);
      if (secret) validateSettings(section.schema, secret);
      return connectorApi(client, workspace.id).create({
        name,
        type,
        config: jsonObject(JSON.stringify(config)),
        credential: secret ? jsonObject(JSON.stringify(secret)) : null,
      });
    },
    onSuccess: done,
  });
  const test = useMutation({
    mutationFn: () => {
      if (!basis) throw new Error(t("Save the provider first."));
      return client
        .workspace(workspace.id)
        .POST("/api/v1/connector-providers/{provider_id}/test", {
          params: { path: { provider_id: basis.id } },
        })
        .then(data);
    },
  });
  function submit(event: React.FormEvent) {
    event.preventDefault();
    save.mutate();
  }
  if (!basis)
    return (
      <form className={providerStyles.connectForm} onSubmit={submit}>
        <ProviderConnectFields
          credentialSchema={
            section.mode === "forbidden" ? undefined : section.schema
          }
          configurationSchema={definition?.configuration_schema}
          credential={section.credential}
          onCredentialChange={section.setCredential}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyLink(definition)}
          advancedOpen={advancedOpen}
          onAdvancedOpenChange={setAdvancedOpen}
        >
          {type === "composio" && <OAuthCallbackSetup />}
        </ProviderConnectFields>
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={t("Add provider")}
        />
      </form>
    );
  return (
    <ProviderEditor onSubmit={submit}>
      <ProviderName value={name} onChange={setName} />
      <ProviderGroup>
        <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        {section.visible && (
          <CredentialRow
            label={t(credentialLabel(section.schema))}
            configured={section.removable}
            removing={section.removing}
            onRemovingChange={section.setRemoving}
            onDiscard={() => section.setCredential({})}
          >
            {section.mode !== "forbidden" && (
              <SchemaFields
                secret
                autoFocus
                labelAction={
                  providerKeyLink(definition) && (
                    <ProviderKeyLink {...providerKeyLink(definition)!} />
                  )
                }
                schema={section.schema}
                requireFields={section.requireFields}
                value={section.credential}
                onChange={section.setCredential}
              />
            )}
          </CredentialRow>
        )}
        <ProviderFacts
          configuration={configuration}
          schema={definition?.configuration_schema}
          only={["endpoint"]}
        />
      </ProviderGroup>
      {type === "composio" && <OAuthCallbackSetup />}
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save changes")}
        leading={
          <ConnectionTest
            placement="footer"
            description="May consume quota or incur cost."
            retry={() => void reload()}
            dirty={
              save.isPending ||
              name !== basis.name ||
              enabled !== basis.enabled ||
              Object.keys(section.credential).length > 0 ||
              section.removing
            }
            action={async () => {
              const result = providerTestResult(await test.mutateAsync());
              return result.success
                ? {
                    ...result,
                    message: t(
                      "OAuth callback configuration and upstream account credentials were not tested.",
                    ),
                  }
                : result;
            }}
          />
        }
      />
    </ProviderEditor>
  );
}
