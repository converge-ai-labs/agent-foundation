import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
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
import { Confirm } from "../../shared/dialogs";
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
  const items = (sources.data ?? []).filter((source) =>
    source.name.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <Page
      title={t("Metrics data sources")}
      description={t(
        "Configure collection, export and historical queries independently.",
      )}
      back={`${basePath}/monitoring`}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search data sources")}
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
                        : (source.config.query?.endpoint ??
                          t("No historical query endpoint"))
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
          <CollectionFooter count={items.length} />
        </>
      )}
      {editing && (
        <SourceEditor
          key={editing === "new" ? "new" : editing.id}
          source={editing === "new" ? undefined : editing}
          onClose={() => setEditing(undefined)}
        />
      )}
    </Page>
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
  onClose,
}: {
  source?: Source;
  onClose: () => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const http = client.workspace(workspace.id),
    readonly = !!source?.builtin || !can("admin");
  const [basis] = useState(source),
    [name, setName] = useState(source?.name ?? ""),
    [enabled, setEnabled] = useState(source?.enabled ?? true);
  const [collection, setCollection] = useState<
    Schema["Configuration"]["collection"]
  >(source?.config.collection ?? "native");
  const [collector, setCollector] = useState<"vmagent" | "alloy">(
    source?.config.collector ?? "vmagent",
  );
  const [exportUrl, setExportUrl] = useState(
      source?.config.export?.endpoint ?? "",
    ),
    [queryUrl, setQueryUrl] = useState(source?.config.query?.endpoint ?? "");
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
  const capabilities = useQuery({
    queryKey: ["metrics-capabilities", workspace.id],
    queryFn: ({ signal }) =>
      http
        .GET("/api/v1/metrics-data-sources/capabilities", { signal })
        .then(data),
  });
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
        query_credentials: queryUrl
          ? credentialPayload(queryCredential)
          : undefined,
      };
      if (basis)
        return http
          .PATCH("/api/v1/metrics-data-sources/{source_id}", {
            params: { path: { source_id: basis.id } },
            headers: ifMatch(rowTag(basis)),
            body,
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
      open
      onOpenChange={(value) => {
        if (!value && !save.isPending) onClose();
      }}
      title={source?.name ?? t("Add data source")}
      description={
        source?.builtin
          ? t("Local Cache is temporary history owned by the Service.")
          : t(
              "Credentials stay on the Service. Query access and export delivery are separate capabilities.",
            )
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
            label={t("Enabled")}
            readOnly={readonly}
            value={enabled ? "yes" : "no"}
            options={[
              { value: "yes", label: t("Enabled") },
              { value: "no", label: t("Disabled") },
            ]}
            onValueChange={(value) => setEnabled(value === "yes")}
          />
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
            onValueChange={(value) => setCollection(value as typeof collection)}
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
              <p>
                {t(
                  "The collector uses its operator-configured export pipeline. Missing deployment configuration disables selection.",
                )}
              </p>
            </>
          )}
          {exporting && (
            <>
              <FormField
                label={t("OTLP endpoint")}
                description={t(
                  "HTTP uses the full metrics URL; gRPC uses the receiver origin. Managed native changes apply on new targets.",
                )}
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
            </>
          )}
          <FormField
            label={t("Prometheus query endpoint")}
            description={t(
              "API base URL before /api/v1/query. Leave empty for collection without historical queries.",
            )}
          >
            <Input
              type="url"
              required={collection === "external"}
              readOnly={readonly}
              value={queryUrl}
              onChange={(e) => setQueryUrl(e.target.value)}
            />
          </FormField>
          {queryUrl && (
            <>
              <details>
                <summary>{t("Scope labels")}</summary>
                <TextAreaField
                  label={t("Scope labels JSON")}
                  readOnly={readonly}
                  value={labels}
                  onChange={setLabels}
                  code
                />
              </details>
              {!readonly && (
                <CredentialEditor
                  label={t("Query credentials")}
                  saved={source?.has_query_credentials ?? false}
                  value={queryCredential}
                  onChange={setQueryCredential}
                />
              )}
            </>
          )}
          {readonly && !source?.builtin && (
            <p>
              {t("Export credentials")}:{" "}
              {t(source?.has_export_credentials ? "Saved" : "Not configured")} ·{" "}
              {t("Query credentials")}:{" "}
              {t(source?.has_query_credentials ? "Saved" : "Not configured")}
            </p>
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
