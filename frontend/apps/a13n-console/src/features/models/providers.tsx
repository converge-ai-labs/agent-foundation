import { PageActions } from "../../shared/page-actions";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Dialog, Input, SelectField, Switch } from "a13n-ui";
import { Plus, PlugZap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { Pagination, Table, useCursor } from "../../shared/collection";
import { SchemaFields } from "../../shared/schema-fields";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";
import { ModelEditor } from "./model-editor";
import styles from "../../shared/shared.module.css";

export function Providers({ scope }: { scope: ModelScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organization, organizationAdmin } = useAccess(),
    page = useCursor();
  const api = modelApi(client, scope);
  const query = useQuery({
    queryKey: ["model-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <ProviderEditor scope={scope} />}</PageActions>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Provider"),
                render: (item) => (
                  <>
                    <PlugZap size={14} /> {item.name}
                    <small>{item.type}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) => (
                  <Badge>
                    {t(item.workspace_id ? "Workspace" : "Organization")}
                  </Badge>
                ),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    item.credential_configured
                      ? "Configured"
                      : "Not configured",
                  ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge state={item.enabled ? "enabled" : "disabled"} />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => {
                  const owner: ModelScope = item.workspace_id
                    ? { kind: "workspace", id: item.workspace_id }
                    : { kind: "organization", id: organization.id };
                  return (
                    (item.workspace_id ? manage : organizationAdmin) && (
                      <div className={styles.actions}>
                        <ProviderEditor scope={owner} providerId={item.id} />
                        <ProviderTest scope={owner} providerId={item.id} />
                        {definitions.data?.items.find(
                          (definition) => definition.type === item.type,
                        )?.supports_model_discovery && (
                          <Discovery scope={scope} provider={item} />
                        )}
                      </div>
                    )
                  );
                },
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("Connect your first provider")}
          description={t(
            "Add an account or endpoint, then choose the models your agents can use.",
          )}
        />
      )}
    </div>
  );
}
function ProviderEditor({
  scope,
  providerId,
}: {
  scope: ModelScope;
  providerId?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const api = modelApi(client, scope);
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const resource = useQuery({
    queryKey: ["model-provider", scope.kind, scope.id, providerId],
    enabled: open && !!providerId,
    queryFn: ({ signal }) => api.provider(providerId!, signal),
  });
  return (
    <Dialog
      title={t(providerId ? "Edit provider" : "Add provider")}
      description={t(
        "Credentials are stored securely and never returned by the service.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button
          size={providerId ? "sm" : "md"}
          variant={providerId ? "secondary" : "primary"}
          icon={!providerId && <Plus size={14} />}
        >
          {t(providerId ? "Edit" : "Add provider")}
        </Button>
      }
    >
      {open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading />
        ) : (!definitions.data && definitions.error) ||
          (!resource.data && resource.error) ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
        ) : (
          definitions.data && (
            <ProviderForm
              key={generation}
              reload={async () => {
                const result = await resource.refetch();
                if (!result.error) setGeneration((value) => value + 1);
              }}
              scope={scope}
              resource={providerId ? resource.data : undefined}
              definitions={definitions.data.items}
              close={() => setOpen(false)}
            />
          )
        ))}
    </Dialog>
  );
}
function ProviderForm({
  scope,
  resource,
  definitions,
  close,
  reload,
}: {
  reload: () => Promise<void>;
  scope: ModelScope;
  resource?: { value: Schema["ModelProvider"]; etag?: string };
  definitions: Schema["ModelProviderDefinition"][];
  close: () => void;
}) {
  const [original] = useState(resource),
    { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [type, setType] = useState(
      original?.value.type ?? definitions[0]?.type ?? "",
    ),
    [name, setName] = useState(original?.value.name ?? ""),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      original?.value.configuration ?? {},
    ),
    [credential, setCredential] = useState(""),
    [removeCredential, setRemoveCredential] = useState(false),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (!definition) throw new Error(t("Choose a provider type."));
      validateSettings(definition.configuration_schema, configuration);
      const body = {
        name,
        configuration,
        enabled,
        ...(removeCredential
          ? { credential: null }
          : credential
            ? { credential }
            : {}),
      };
      if (!original) return api.createProvider({ ...body, type });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, body);
    },
    onSuccess: () => {
      setCredential("");
      void cache.invalidateQueries();
      close();
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <Input
        label={t("Name")}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={128}
      />
      <SelectField
        label={t("Provider type")}
        placeholder={t("Choose a provider…")}
        value={type}
        disabled={!!original}
        onValueChange={(value) => {
          setType(value);
          setConfiguration({});
          setCredential("");
        }}
        options={definitions.map((item) => ({
          value: item.type,
          label: item.display_name,
        }))}
      />
      {definition && (
        <SchemaFields
          key={type}
          schema={definition.configuration_schema}
          value={configuration}
          onChange={setConfiguration}
        />
      )}
      <Input
        label={t("Credential")}
        type="password"
        autoComplete="off"
        value={credential}
        onChange={(event) => {
          setCredential(event.target.value);
          setRemoveCredential(false);
        }}
        hint={t(
          original?.value.credential_configured
            ? "Leave empty to keep the current credential."
            : "Enter the credential required by this provider.",
        )}
      />
      {original?.value.credential_configured && (
        <Switch
          label={t("Remove stored credential")}
          checked={removeCredential}
          onCheckedChange={setRemoveCredential}
        />
      )}
      <Switch
        label={t("Enabled")}
        checked={enabled}
        onCheckedChange={setEnabled}
      />
      <ErrorNotice
        error={save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions pending={save.isPending} />
    </form>
  );
}
function ProviderTest({
  scope,
  providerId,
}: {
  scope: ModelScope;
  providerId: string;
}) {
  const api = modelApi(useClient(), scope),
    { t } = useTranslation();
  const test = useMutation({ mutationFn: () => api.testProvider(providerId) });
  return (
    <Dialog
      title={t("Test provider")}
      description={t(
        "This contacts the provider and may consume quota or incur cost.",
      )}
      closeLabel={t("Close")}
      trigger={<Button size="sm">{t("Test")}</Button>}
    >
      <Button onClick={() => test.mutate()} loading={test.isPending}>
        {t("Run connection test")}
      </Button>
      <ErrorNotice error={test.error} />
      {test.data && (
        <p role="status">
          <StateBadge state={test.data.success ? "succeeded" : "failed"} />{" "}
          {test.data.message} · {test.data.elapsed_ms} ms
        </p>
      )}
    </Dialog>
  );
}
function Discovery({
  scope,
  provider,
}: {
  scope: ModelScope;
  provider: Schema["ModelProvider"];
}) {
  const api = modelApi(useClient(), scope),
    { t } = useTranslation(),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(0);
  const discover = useMutation({ mutationFn: () => api.discover(provider.id) });
  const candidates =
    discover.data?.items.filter((item) =>
      `${item.upstream_model} ${item.display_name}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) ?? [];
  return (
    <Dialog
      title={t("Discover models")}
      description={t(
        "Candidates are suggestions. Add a model to make it available to agents.",
      )}
      closeLabel={t("Close")}
      trigger={<Button size="sm">{t("Discover")}</Button>}
    >
      <div className={styles.stack}>
        <Button
          loading={discover.isPending}
          onClick={() => {
            setPage(0);
            discover.mutate();
          }}
        >
          {t("Refresh catalog")}
        </Button>
        <ErrorNotice error={discover.error} />
        {discover.data && (
          <>
            <Input
              label={t("Search models")}
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
              }}
            />
            {candidates.slice(page * 10, page * 10 + 10).map((candidate) => (
              <div key={candidate.upstream_model} className={styles.toolbar}>
                <span>
                  {candidate.display_name ?? candidate.upstream_model}
                  <small className={styles.muted}>
                    {candidate.upstream_model}
                  </small>
                </span>
                <ModelEditor
                  scope={scope}
                  candidate={candidate}
                  providerId={provider.id}
                />
              </div>
            ))}
            {!candidates.length && (
              <p>{t("No models found. You can still add a model manually.")}</p>
            )}
            <div className={styles.pagination}>
              <Button disabled={!page} onClick={() => setPage(page - 1)}>
                {t("Previous")}
              </Button>
              <Button
                disabled={(page + 1) * 10 >= candidates.length}
                onClick={() => setPage(page + 1)}
              >
                {t("Next")}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
