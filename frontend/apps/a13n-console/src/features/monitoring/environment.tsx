import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { monitoringApi } from "./api";
import { statusLabels } from "./labels";
import styles from "./monitoring.module.css";

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
    <section className={styles.fields}>
      <h3>{t("Monitoring")}</h3>
      <div className={styles.queryHeading}>
        <span>
          {source?.name ??
            environment.metrics_data_source_id ??
            t("No monitoring")}
        </span>
        {can("write") && !editing && (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            {t("Configure")}
          </Button>
        )}
      </div>
      <ErrorNotice error={sources.error ?? collector.error} />
      {source?.config.collection === "native" && (
        <p>
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
        <p>
          {t("Collector")}:{" "}
          {statusLabels(t)[collector.data?.state ?? "pending"] ??
            collector.data?.state}
          .{" "}
          <span
            title={t(
              "Reload acceptance does not confirm scrape or export health.",
            )}
          >
            {t("Check delivery at the receiver.")}
          </span>
        </p>
      )}
      {source?.config.collection === "proxy" && <p>{t("Temporary history")}</p>}
      {editing && (
        <BindingEditor
          environment={environment}
          etag={etag}
          onClose={() => setEditing(false)}
        />
      )}
      <Button
        variant="outline"
        size="sm"
        render={
          <Link
            to={`${basePath}/monitoring?target=environment:${environment.id}`}
          />
        }
      >
        {t("Open dashboard")}
      </Button>
    </section>
  );
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
    [labels, setLabels] = useState(
      JSON.stringify(environment.metrics_labels ?? {}, null, 2),
    );
  const save = useMutation({
    mutationFn: async () => {
      if (!basis.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const parsed: unknown = JSON.parse(labels);
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) ||
        Object.values(parsed).some((value) => typeof value !== "string")
      )
        throw new Error(
          t("Scope labels must be a JSON object with string values."),
        );
      return client
        .workspace(environment.workspace_id)
        .PUT("/api/v1/environments/{environment_id}/metrics-binding", {
          params: { path: { environment_id: environment.id } },
          headers: ifMatch(basis.etag),
          body: {
            metrics_data_source_id: source || null,
            metrics_labels: source ? (parsed as Record<string, string>) : {},
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
      className={styles.fields}
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
      <details>
        <summary>{t("Metric identity labels")}</summary>
        <TextAreaField
          label={t("Scope labels JSON")}
          value={labels}
          onChange={setLabels}
          readOnly={!can("admin") || save.isPending}
          code
        />
      </details>
      <ErrorNotice error={save.error} />
      <FormActions
        label={t("Save selection")}
        pending={save.isPending}
        onCancel={onClose}
      />
    </form>
  );
}
