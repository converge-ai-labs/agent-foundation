import { FormSection } from "../../shared/forms";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  ReadOnlyField,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
} from "../../shared/collection";
import { useMountedModal, Confirm } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import {
  FormActions,
  HeaderFields,
  type HeaderDraft,
  TextAreaField,
} from "../../shared/forms";
import { Page, PageActions } from "../../shared/page";
import { monitoringApi, type Source } from "./api";
import styles from "./monitoring.module.css";

export function DataSourcesPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useState(""),
    [editing, setEditing] = useState<Source | "new">();
  const sources = useQuery(monitoringApi(client, workspace.id).sources());
  const [route, setRoute] = useState("");
  const [status, setStatus] = useState("");
  const items = (sources.data ?? []).filter(
    (source) =>
      [
        source.name,
        source.id,
        source.config.query?.endpoint,
        source.config.export?.endpoint,
      ]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (!route || source.config.collection === route) &&
      (!status || source.enabled === (status === "enabled")),
  );
  return (
    <Page
      title={t("Metrics data sources")}
      back={`${basePath}/monitoring`}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search data sources")}
          filters={
            <>
              <ChoiceField
                label={t("Collection")}
                variant="filter"
                value={route}
                options={[
                  { value: "", label: t("All") },
                  ...["native", "pull", "proxy", "external"].map((value) => ({
                    value,
                    label: t(value),
                  })),
                ]}
                onValueChange={setRoute}
              />
              <ChoiceField
                label={t("Status")}
                variant="filter"
                value={status}
                options={[
                  { value: "", label: t("All") },
                  { value: "enabled", label: t("Enabled") },
                  { value: "disabled", label: t("Disabled") },
                ]}
                onValueChange={setStatus}
              />
              {(search || route || status) && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setSearch("");
                    setRoute("");
                    setStatus("");
                  }}
                >
                  {t("Clear filters")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      <PageActions>
        {can("admin") && (
          <Button onClick={() => setEditing("new")}>
            {t("Add data source")}
          </Button>
        )}
      </PageActions>
      <ErrorNotice error={sources.error} />
      {sources.isPending ? (
        <Loading variant="table" columns={4} />
      ) : (
        <>
          <ResourceTable
            caption={t("Metrics data sources")}
            items={items}
            onRowActivate={(source) => setEditing(source)}
            columns={[
              {
                label: t("Name"),
                tone: "primary",
                render: (source) => (
                  <ResourceIdentity
                    name={source.name}
                    resourceId={source.id}
                    description={
                      source.builtin
                        ? t("Built-in temporary cache")
                        : source.config.query?.endpoint
                    }
                  />
                ),
              },
              {
                label: t("Collection"),
                render: (source) =>
                  source.config.collector ?? t(source.config.collection),
              },
              {
                label: t("Export"),
                render: (source) => source.config.export?.endpoint ?? "—",
              },
              {
                label: t("Status"),
                render: (source) => t(source.enabled ? "Enabled" : "Disabled"),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} data sources", { count: items.length })}
          />
        </>
      )}
      {editing === "new" ? (
        <NewSourceEditor onClose={() => setEditing(undefined)} />
      ) : editing ? (
        <SourceEditor
          key={editing.id}
          source={editing}
          onClose={() => setEditing(undefined)}
        />
      ) : null}
    </Page>
  );
}

function NewSourceEditor({ onClose: onClosed }: { onClose: () => void }) {
  const { modalProps } = useMountedModal(onClosed);
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const preset = useQuery(
    monitoringApi(client, workspace.id).deploymentPreset(),
  );
  const [manual, setManual] = useState(false);
  if (modalProps.open && (manual || preset.data !== undefined))
    return (
      <SourceEditor
        preset={manual ? undefined : (preset.data ?? undefined)}
        onClose={onClosed}
      />
    );
  return (
    <ModalFrame
      {...modalProps}
      title={t("Add data source")}
      closeLabel={t("Close")}
      size="lg"
    >
      {preset.isPending ? (
        <Loading />
      ) : (
        <>
          <ErrorNotice error={preset.error} />
          <Button onClick={() => void preset.refetch()}>{t("Retry")}</Button>
          <Button variant="ghost" onClick={() => setManual(true)}>
            {t("Configure manually")}
          </Button>
        </>
      )}
    </ModalFrame>
  );
}

type CredentialDraft = {
  mode: "keep" | "replace" | "clear";
  rows: HeaderDraft[];
};
const untouched = (): CredentialDraft => ({ mode: "keep", rows: [] });
function credentialPayload(
  value: CredentialDraft,
): Schema["Credentials"] | null | undefined {
  if (value.mode === "keep") return undefined;
  if (value.mode === "clear") return null;
  if (!value.rows.length)
    throw new Error("Add at least one header to replace credentials.");
  const headers: Record<string, string> = {};
  for (const row of value.rows) {
    const name = row.name.trim().toLowerCase();
    if (!name || !row.value || name in headers)
      throw new Error(
        "Header names must be nonempty and unique, and each header needs a value.",
      );
    headers[name] = row.value;
  }
  return { headers };
}
function CredentialEditor({
  label,
  saved,
  value,
  onChange,
}: {
  label: string;
  saved: boolean;
  value: CredentialDraft;
  onChange: (value: CredentialDraft) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.fields}>
      <div className={styles.queryHeading}>
        <span>
          {label} · {t(saved ? "Saved" : "Not configured")}
        </span>
        <div>
          {value.mode === "keep" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => onChange({ mode: "replace", rows: [] })}
            >
              {t(saved ? "Replace" : "Add")}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => onChange(untouched())}
            >
              {t("Keep saved credentials")}
            </Button>
          )}
          {saved && value.mode !== "clear" && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => onChange({ mode: "clear", rows: [] })}
            >
              {t("Clear")}
            </Button>
          )}
        </div>
      </div>
      {value.mode === "replace" && (
        <HeaderFields
          rows={value.rows}
          onChange={(rows) => onChange({ ...value, rows })}
        />
      )}
      {value.mode === "clear" && (
        <p>{t("Credentials will be removed when you save.")}</p>
      )}
    </div>
  );
}
function SourceEditor({
  source,
  preset: initialPreset,
  onClose: onClosed,
}: {
  source?: Source;
  preset?: Schema["DeploymentPreset"];
  onClose: () => void;
}) {
  const { close: onClose, modalProps } = useMountedModal(onClosed);
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const http = client.workspace(workspace.id),
    readonly = !!source?.builtin || !can("admin");
  const [preset] = useState(initialPreset);
  const [basis] = useState(source),
    [name, setName] = useState(source?.name ?? preset?.name ?? ""),
    [enabled, setEnabled] = useState(source?.enabled ?? true);
  const [collection, setCollection] = useState<
    Schema["Configuration"]["collection"]
  >(source?.config.collection ?? preset?.config.collection ?? "native");
  const [collector, setCollector] = useState<"vmagent" | "alloy">(
    source?.config.collector ?? preset?.config.collector ?? "vmagent",
  );
  const [exportUrl, setExportUrl] = useState(
      source?.config.export?.endpoint ?? "",
    ),
    [queryUrl, setQueryUrl] = useState(
      source?.config.query?.endpoint ?? preset?.config.query?.endpoint ?? "",
    );
  const [protocol, setProtocol] = useState(
      source?.config.export?.protocol ?? "http/protobuf",
    ),
    [interval, setInterval] = useState(
      source?.config.export?.interval_seconds ?? 30,
    );
  const [labels, setLabels] = useState(
    JSON.stringify(source?.config.query?.scope_labels ?? {}, null, 2),
  );
  const [exportCredential, setExportCredential] = useState(untouched),
    [queryCredential, setQueryCredential] = useState(untouched);
  const [useDeploymentCredential, setUseDeploymentCredential] = useState(false);
  const deploymentCredentialAvailable =
    !!preset?.has_query_credentials &&
    queryUrl.replace(/\/+$/, "") === preset.config.query?.endpoint;
  const capabilities = useQuery(
    monitoringApi(client, workspace.id).capabilities(),
  );
  const exporting = collection === "native" || collection === "proxy";
  const done = () => {
    void cache.invalidateQueries({
      queryKey: ["metrics-data-sources", workspace.id],
    });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () => {
      const scope: unknown = JSON.parse(labels);
      if (
        !scope ||
        typeof scope !== "object" ||
        Array.isArray(scope) ||
        Object.values(scope).some((value) => typeof value !== "string")
      )
        throw new Error(
          t("Scope labels must be a JSON object with string values."),
        );
      const body: Schema["DataSourceCreate"] = {
        name,
        enabled,
        config: {
          collection,
          collector: collection === "pull" ? collector : null,
          export:
            exporting && exportUrl
              ? {
                  endpoint: exportUrl,
                  protocol: collection === "proxy" ? "http/protobuf" : protocol,
                  interval_seconds: interval,
                }
              : null,
          query: queryUrl
            ? {
                endpoint: queryUrl,
                scope_labels: scope as Record<string, string>,
              }
            : null,
        },
        export_credentials:
          exporting && exportUrl
            ? credentialPayload(exportCredential)
            : undefined,
        deployment_preset_revision: preset?.revision,
        use_deployment_query_credentials: useDeploymentCredential,
        query_credentials:
          queryUrl && !useDeploymentCredential
            ? credentialPayload(queryCredential)
            : undefined,
      };
      if (basis)
        return http
          .PATCH("/api/v1/metrics-data-sources/{source_id}", {
            params: { path: { source_id: basis.id } },
            headers: ifMatch(rowTag(basis)),
            body: {
              name: body.name,
              enabled: body.enabled,
              config: body.config,
              export_credentials: body.export_credentials,
              query_credentials: body.query_credentials,
            },
          })
          .then(data);
      return http.POST("/api/v1/metrics-data-sources", { body }).then(data);
    },
    onSuccess: done,
  });
  const probe = useMutation({
    mutationFn: () =>
      http
        .POST("/api/v1/metrics-data-sources/{source_id}/query", {
          params: { path: { source_id: basis!.id } },
          body: { kind: "instant", expression: "vector(1)" },
        })
        .then(data),
  });
  return (
    <ModalFrame
      {...modalProps}
      title={source?.name ?? t("Add data source")}
      description={
        source?.builtin
          ? t("Local Cache is temporary history owned by the Service.")
          : undefined
      }
      closeLabel={t("Close")}
      size="lg"
    >
      <form
        className={styles.fields}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset disabled={save.isPending} className={styles.fields}>
          {preset && (
            <p>
              {t(
                "Deployment defaults are prefilled. Confirm to create a source in this workspace; Environments remain unchanged.",
              )}
            </p>
          )}
          <FormSection title={t("Basic information")}>
            <FormField label={t("Name")}>
              <Input
                required
                maxLength={128}
                readOnly={readonly}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </FormField>
            <ChoiceField
              label={t("Status")}
              readOnly={readonly}
              value={enabled ? "yes" : "no"}
              options={[
                { value: "yes", label: t("Enabled") },
                { value: "no", label: t("Disabled") },
              ]}
              onValueChange={(value) => setEnabled(value === "yes")}
            />
          </FormSection>
          <FormSection title={t("Collection")}>
            <ChoiceField
              label={t("Collection")}
              readOnly={readonly}
              value={collection}
              options={[
                { value: "native", label: t("Native OTLP push") },
                {
                  value: "pull",
                  label: t("Collector pull"),
                  disabled: !capabilities.data?.collectors.some(
                    (item) => item.available,
                  ),
                },
                {
                  value: "proxy",
                  label: t("Service proxy"),
                  disabled: !capabilities.data?.proxy,
                },
                { value: "external", label: t("External collection") },
              ]}
              onValueChange={(value) =>
                setCollection(value as typeof collection)
              }
            />
            <ErrorNotice error={capabilities.error} />
            {collection === "pull" && (
              <>
                <ChoiceField
                  label={t("Collector")}
                  value={collector}
                  readOnly={readonly}
                  options={["vmagent", "alloy"].map((value) => ({
                    value,
                    label: value,
                    disabled: !capabilities.data?.collectors.find(
                      (item) => item.kind === value,
                    )?.available,
                  }))}
                  onValueChange={(value) =>
                    setCollector(value === "alloy" ? "alloy" : "vmagent")
                  }
                />
                <small>{t("Export is configured on the collector.")}</small>
              </>
            )}
          </FormSection>
          {exporting && (
            <FormSection title={t("Export")}>
              <FormField
                label={t("OTLP endpoint")}
                description={
                  readonly
                    ? undefined
                    : t(
                        protocol === "grpc"
                          ? "Receiver origin, for example https://otel.example.com:4317"
                          : "Full metrics URL, for example https://otel.example.com/v1/metrics",
                      )
                }
              >
                <Input
                  type="url"
                  required={collection === "native"}
                  readOnly={readonly}
                  value={exportUrl}
                  onChange={(e) => setExportUrl(e.target.value)}
                />
              </FormField>
              <div className={styles.row}>
                <ChoiceField
                  label={t("Protocol")}
                  readOnly={readonly || collection === "proxy"}
                  value={collection === "proxy" ? "http/protobuf" : protocol}
                  options={[
                    { value: "http/protobuf", label: "HTTP/protobuf" },
                    { value: "grpc", label: "gRPC" },
                  ]}
                  onValueChange={(value) =>
                    setProtocol(value === "grpc" ? "grpc" : "http/protobuf")
                  }
                />
                <FormField label={t("Export interval (seconds)")}>
                  <Input
                    type="number"
                    min={5}
                    max={300}
                    readOnly={readonly}
                    value={interval}
                    onChange={(e) => setInterval(Number(e.target.value))}
                  />
                </FormField>
              </div>
              {!readonly && exportUrl && (
                <CredentialEditor
                  label={t("Export credentials")}
                  saved={source?.has_export_credentials ?? false}
                  value={exportCredential}
                  onChange={setExportCredential}
                />
              )}
            </FormSection>
          )}
          <FormSection title={t("Query")}>
            <FormField
              label={t("Prometheus query endpoint")}
              description={
                readonly ? undefined : t("Base URL before /api/v1/query.")
              }
            >
              <Input
                type="url"
                required={collection === "external"}
                readOnly={readonly}
                value={queryUrl}
                onChange={(e) => {
                  setQueryUrl(e.target.value);
                  setUseDeploymentCredential(false);
                }}
              />
            </FormField>
            {queryUrl && (
              <>
                <DisclosureSection title={t("Scope labels")}>
                  <TextAreaField
                    label={t("Scope labels JSON")}
                    readOnly={readonly}
                    value={labels}
                    onChange={setLabels}
                    code
                  />
                </DisclosureSection>
                {!readonly && preset?.has_query_credentials && (
                  <ChoiceField
                    label={t("Use deployment query credentials")}
                    value={useDeploymentCredential ? "yes" : "no"}
                    options={[
                      { value: "no", label: t("No") },
                      {
                        value: "yes",
                        label: t("Yes"),
                        disabled: !deploymentCredentialAvailable,
                      },
                    ]}
                    onValueChange={(value) =>
                      setUseDeploymentCredential(value === "yes")
                    }
                  />
                )}
                {useDeploymentCredential && (
                  <small>
                    {t(
                      "The server saves a credential snapshot. Later deployment changes do not update this source.",
                    )}
                  </small>
                )}
                {!readonly && !useDeploymentCredential && (
                  <CredentialEditor
                    label={t("Query credentials")}
                    saved={source?.has_query_credentials ?? false}
                    value={queryCredential}
                    onChange={setQueryCredential}
                  />
                )}
              </>
            )}
          </FormSection>
          {readonly && !source?.builtin && (
            <div className={styles.row}>
              <ReadOnlyField label={t("Export credentials")}>
                {t(source?.has_export_credentials ? "Saved" : "Not configured")}
              </ReadOnlyField>
              <ReadOnlyField label={t("Query credentials")}>
                {t(source?.has_query_credentials ? "Saved" : "Not configured")}
              </ReadOnlyField>
            </div>
          )}
          {collection === "native" && basis && !readonly && (
            <small>{t("Changes apply to newly provisioned targets.")}</small>
          )}
          <ErrorNotice error={save.error ?? probe.error} />
          {probe.isSuccess && (
            <p role="status">
              {t("Saved query endpoint responded successfully.")}
            </p>
          )}
          <FormActions
            pending={save.isPending}
            label={t("Save data source")}
            dismiss={readonly}
            cancelLabel={readonly ? t("Close") : undefined}
            onCancel={onClose}
            leading={
              basis?.config.query &&
              can("admin") && (
                <Button
                  type="button"
                  variant="outline"
                  loading={probe.isPending}
                  onClick={() => probe.mutate()}
                >
                  {t("Test saved query endpoint")}
                </Button>
              )
            }
          />
        </fieldset>
      </form>
      {basis && !readonly && !save.isPending && (
        <Confirm
          title={t("Delete data source")}
          subject={basis.name}
          description={t(
            "Unbind this source from Environments and Devices before deleting it.",
          )}
          trigger={t("Delete data source")}
          triggerVariant="ghost"
          danger
          action={() =>
            http.DELETE("/api/v1/metrics-data-sources/{source_id}", {
              params: { path: { source_id: basis.id } },
              headers: ifMatch(rowTag(basis)),
            })
          }
          onSuccess={done}
        />
      )}
    </ModalFrame>
  );
}
