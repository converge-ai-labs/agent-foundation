import { PlusIcon, XIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, DisclosureSection, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import styles from "../environments/environments.module.css";
import { Fact } from "../environments/fact";
import { monitoringApi } from "./api";
import { statusLabels } from "./labels";

export function MetricsSourcePicker({
  value,
  onChange,
  external = false,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  external?: boolean;
  disabled?: boolean;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const api = monitoringApi(client, workspace.id);
  const sources = useQuery(api.sources());
  const capabilities = useQuery(api.capabilities());
  return (
    <>
      <ChoiceField
        label={t("Metrics data source")}
        value={value}
        disabled={disabled}
        options={[
          { value: "", label: t("No monitoring") },
          ...(value && !sources.data?.some((source) => source.id === value)
            ? [
                {
                  value,
                  label: `${t("Unavailable data source")} · ${value}`,
                  disabled: true,
                },
              ]
            : []),
          ...(sources.data ?? []).map((source) => ({
            value: source.id,
            label: source.name,
            disabled:
              !source.enabled ||
              (external && !source.builtin && !can("admin")) ||
              (source.config.collection === "proxy" &&
                !capabilities.data?.proxy) ||
              (source.config.collection === "pull" &&
                !capabilities.data?.collectors.some(
                  (collector) =>
                    collector.kind === source.config.collector &&
                    collector.available,
                )),
          })),
        ]}
        onValueChange={onChange}
      />
      <ErrorNotice error={sources.error ?? capabilities.error} />
    </>
  );
}
export function EnvironmentMonitoring({
  environment,
  etag,
}: {
  environment: Schema["EnvironmentView"];
  etag?: string;
}) {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const sources = useQuery(monitoringApi(client, workspace.id).sources());
  const source = sources.data?.find(
    (item) => item.id === environment.metrics_data_source_id,
  );
  const collector = useQuery({
    queryKey: ["metrics-collector", workspace.id, environment.id],
    enabled: source?.config.collection === "pull",
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/environments/{environment_id}/metrics/collector", {
          params: { path: { environment_id: environment.id } },
          signal,
        })
        .then(data),
    refetchInterval: 30000,
  });
  const application = environment.metrics_application;
  const applied =
    !!source &&
    application?.source_id === source.id &&
    application.source_version === source.version &&
    !!application.applied_at;
  return (
    <section className={styles.factGroup}>
      <h3>{t("Monitoring")}</h3>
      <dl className={styles.facts}>
        <Fact label={t("Data source")}>
          <span>
            {source?.name ??
              environment.metrics_data_source_id ??
              t("No monitoring")}
          </span>
          {can("write") && !editing && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setEditing(true)}
            >
              {t("Configure")}
            </Button>
          )}
        </Fact>
        {source?.config.collection === "pull" && (
          <Fact label={t("Collector")}>
            {statusLabels(t)[collector.data?.state ?? "pending"] ??
              collector.data?.state}
          </Fact>
        )}
      </dl>
      <ErrorNotice error={sources.error ?? collector.error} />
      {source?.config.collection === "native" && (
        <p className={styles.panelNote}>
          {t(
            applied
              ? "Native exporter configured. Check delivery at the receiver."
              : environment.template_id
                ? "Pending: provision a new target to apply this change."
                : "Manual exporter setup required.",
          )}
        </p>
      )}
      {source?.config.collection === "pull" && (
        <p
          className={styles.panelNote}
          title={t(
            "Reload acceptance does not confirm scrape or export health.",
          )}
        >
          {t("Check delivery at the receiver.")}
        </p>
      )}
      {source?.config.collection === "proxy" && (
        <p className={styles.panelNote}>{t("Temporary history")}</p>
      )}
      {editing && (
        <BindingEditor
          environment={environment}
          etag={etag}
          onClose={() => setEditing(false)}
        />
      )}
      {environment.metrics_data_source_id && !editing && (
        <Button
          variant="outline"
          size="sm"
          className={styles.groupAction}
          render={
            <Link
              to={`${basePath}/monitoring?target=environment:${environment.id}`}
            />
          }
        >
          {t("Open dashboard")}
        </Button>
      )}
    </section>
  );
}

type LabelRow = { id: string; key: string; value: string };

function labelRows(labels: Record<string, string> = {}): LabelRow[] {
  return Object.entries(labels).map(([key, value]) => ({
    id: crypto.randomUUID(),
    key,
    value,
  }));
}

function BindingEditor({
  environment,
  etag,
  onClose,
}: {
  environment: Schema["EnvironmentView"];
  etag?: string;
  onClose: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation();
  const [basis] = useState({ environment, etag }),
    [source, setSource] = useState(environment.metrics_data_source_id ?? ""),
    [labels, setLabels] = useState(() => labelRows(environment.metrics_labels));
  const entered = labels.filter((row) => row.key.trim() || row.value);
  const save = useMutation({
    mutationFn: async () => {
      if (!basis.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      if (entered.some((row) => !row.key.trim()))
        throw new Error(t("Each label needs a name."));
      const named = Object.fromEntries(
        entered.map((row) => [row.key.trim(), row.value]),
      );
      if (Object.keys(named).length < entered.length)
        throw new Error(t("Label names must be unique."));
      return client
        .workspace(environment.workspace_id)
        .PUT("/api/v1/environments/{environment_id}/metrics-binding", {
          params: { path: { environment_id: environment.id } },
          headers: ifMatch(basis.etag),
          body: {
            metrics_data_source_id: source || null,
            metrics_labels: source ? named : {},
          },
        })
        .then(data);
    },
    onSuccess: async () => {
      await cache.invalidateQueries({
        queryKey: ["environment", environment.id],
      });
      void cache.invalidateQueries({ queryKey: ["environments"] });
      onClose();
    },
  });
  return (
    <form
      className={styles.inlineEditor}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <MetricsSourcePicker
        value={source}
        onChange={setSource}
        external={!environment.template_id}
        disabled={save.isPending}
      />
      {source && (
        <DisclosureSection
          title={t("Metric identity labels")}
          summary={
            entered.length
              ? t("{{count}} labels", { count: entered.length })
              : undefined
          }
        >
          <LabelFields
            rows={labels}
            onChange={setLabels}
            readOnly={!can("admin")}
            disabled={save.isPending}
          />
        </DisclosureSection>
      )}
      <ErrorNotice error={save.error} />
      <div className={styles.inlineEditorActions}>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onClose}
          disabled={save.isPending}
        >
          {t("Cancel")}
        </Button>
        <Button
          type="submit"
          size="sm"
          variant="outline"
          loading={save.isPending}
        >
          {t("Save selection")}
        </Button>
      </div>
    </form>
  );
}

/** Name and value rows for the labels every reported series carries. */
function LabelFields({
  rows,
  onChange,
  readOnly,
  disabled,
}: {
  rows: LabelRow[];
  onChange: (rows: LabelRow[]) => void;
  readOnly: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const update = (id: string, patch: Partial<LabelRow>) =>
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  return (
    <div className={styles.labelFields}>
      <p className={styles.panelNote}>
        {t("Labels added to every series this environment reports.")}
      </p>
      {rows.map((row, index) => (
        <div key={row.id} className={styles.labelRow}>
          <Input
            size="sm"
            aria-label={t("Label name {{index}}", { index: index + 1 })}
            placeholder={t("Name")}
            value={row.key}
            readOnly={readOnly}
            disabled={disabled}
            autoComplete="off"
            onChange={(event) => update(row.id, { key: event.target.value })}
          />
          <Input
            size="sm"
            aria-label={t("Label value {{index}}", { index: index + 1 })}
            placeholder={t("Value")}
            value={row.value}
            readOnly={readOnly}
            disabled={disabled}
            autoComplete="off"
            onChange={(event) => update(row.id, { value: event.target.value })}
          />
          {!readOnly && (
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={t("Remove label {{index}}", { index: index + 1 })}
              title={t("Remove")}
              disabled={disabled}
              onClick={() =>
                onChange(rows.filter((item) => item.id !== row.id))
              }
            >
              <XIcon />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className={styles.groupAction}
          disabled={disabled}
          onClick={() =>
            onChange([...rows, { id: crypto.randomUUID(), key: "", value: "" }])
          }
        >
          <PlusIcon aria-hidden="true" />
          {t("Add label")}
        </Button>
      )}
    </div>
  );
}
