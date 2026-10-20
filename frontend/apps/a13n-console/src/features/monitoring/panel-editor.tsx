import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import {
  monitoringApi,
  type Panel,
  type PanelQuery,
  type Target,
  type TimeRange,
} from "./api";
import { MetricsPanel } from "./panel";
import { metricLabel } from "./series";
import { PromQLEditor } from "./promql-editor";
import styles from "./monitoring.module.css";

type Metric = Extract<PanelQuery, { kind: "metric" }>["metric"];
export const metrics: Metric[] = [
  "system_cpu_busy_ratio",
  "system_memory_used_bytes",
  "system_memory_total_bytes",
  "system_memory_available_bytes",
  "system_memory_cache_bytes",
  "system_memory_wired_bytes",
  "system_memory_compressed_bytes",
  "system_swap_used_bytes",
  "system_swap_total_bytes",
  "system_commit_total_bytes",
  "system_commit_limit_bytes",
  "system_processes",
  "system_threads",
  "system_handles",
  "cgroup_memory_current_bytes",
  "cgroup_pids_current",
  "tcp_connections",
  "udp_sockets",
  "daemon_sessions",
  "daemon_processes",
  "daemon_connections",
  "daemon_transfers",
  "daemon_retained_output_bytes",
  "network_received_bytes_per_second",
  "network_transmitted_bytes_per_second",
  "disk_read_bytes_per_second",
  "disk_written_bytes_per_second",
  "disk_read_operations_per_second",
  "disk_write_operations_per_second",
  "cgroup_io_read_bytes_per_second",
  "cgroup_io_written_bytes_per_second",
  "cgroup_io_read_operations_per_second",
  "cgroup_io_write_operations_per_second",
];
export function newPanel(panels: Panel[]): Panel {
  return {
    id: crypto.randomUUID(),
    title: "New panel",
    position: {
      x: 0,
      y: Math.min(
        1000,
        Math.max(0, ...panels.map((p) => p.position.y + p.position.h)),
      ),
      w: 6,
      h: 5,
    },
    visualization: "timeseries",
    unit: "auto",
    legend: true,
    queries: [
      {
        kind: "metric",
        metric: "system_cpu_busy_ratio",
        history: "configured",
      },
    ],
  };
}
export function PanelEditor({
  panel,
  target,
  range,
  onApply,
  onClose,
}: {
  panel: Panel;
  target: Target | null;
  range: TimeRange;
  onApply: (panel: Panel) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const [draft, setDraft] = useState(panel),
    [preview, setPreview] = useState<Panel>(),
    [revision, setRevision] = useState(0);
  const sources = useQuery(monitoringApi(client, workspace.id).sources());
  const update = (change: Partial<Panel>) =>
    setDraft((value) => ({ ...value, ...change }));
  const queryChange = (index: number, query: PanelQuery) =>
    update({
      queries: draft.queries.map((item, i) => (i === index ? query : item)),
    });
  const valid =
    !!draft.title.trim() &&
    draft.queries.every(
      (query) =>
        query.kind === "metric" ||
        (!!query.source_id && !!query.expression.trim()),
    );
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("Edit panel")}
      closeLabel={t("Close")}
    >
      <form
        className={styles.fields}
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) onApply(draft);
        }}
      >
        <FormField label={t("Title")}>
          <Input
            required
            maxLength={128}
            value={draft.title}
            onChange={(e) => update({ title: e.target.value })}
          />
        </FormField>
        <div className={styles.row}>
          <ChoiceField
            label={t("Visualization")}
            value={draft.visualization ?? "timeseries"}
            options={[
              { value: "timeseries", label: t("Time series") },
              { value: "stat", label: t("Statistic") },
            ]}
            onValueChange={(value) =>
              update({
                visualization: value === "stat" ? "stat" : "timeseries",
              })
            }
          />
          <ChoiceField
            label={t("Unit")}
            value={draft.unit ?? "auto"}
            options={(
              [
                "auto",
                "ratio",
                "bytes",
                "bytes_per_second",
                "operations_per_second",
                "count",
                "seconds",
              ] as const
            ).map((value) => ({ value, label: t(value) }))}
            onValueChange={(value) => update({ unit: value as Panel["unit"] })}
          />
          <ChoiceField
            label={t("Legend")}
            value={draft.legend === false ? "off" : "on"}
            options={[
              { value: "on", label: t("On") },
              { value: "off", label: t("Off") },
            ]}
            onValueChange={(value) => update({ legend: value === "on" })}
          />
        </div>
        <details>
          <summary>{t("Description (optional)")}</summary>
          <FormField label={t("Description (optional)")}>
            <Input
              maxLength={2048}
              value={draft.description ?? ""}
              onChange={(e) => update({ description: e.target.value })}
            />
          </FormField>
        </details>
        <details>
          <summary>{t("Panel position and size")}</summary>
          <div className={styles.row}>
            {(["x", "y", "w", "h"] as const).map((key) => (
              <FormField
                key={key}
                label={t(
                  { x: "Column", y: "Row", w: "Width", h: "Height" }[key],
                )}
              >
                <Input
                  type="number"
                  min={key === "w" || key === "h" ? 3 : 0}
                  max={
                    key === "x"
                      ? 12 - draft.position.w
                      : key === "w"
                        ? 12 - draft.position.x
                        : key === "h"
                          ? 16
                          : 1000
                  }
                  required
                  value={draft.position[key]}
                  onChange={(e) =>
                    update({
                      position: {
                        ...draft.position,
                        [key]: Number(e.target.value),
                      },
                    })
                  }
                />
              </FormField>
            ))}
          </div>
        </details>
        <ErrorNotice error={sources.error} />
        {draft.queries.map((query, index) => (
          <div className={styles.query} key={index}>
            <div className={styles.queryHeading}>
              <strong>
                {t("Query")} {String.fromCharCode(65 + index)}
              </strong>
              {draft.queries.length > 1 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    update({
                      queries: draft.queries.filter((_, i) => i !== index),
                    })
                  }
                >
                  {t("Remove query")}
                </Button>
              )}
            </div>
            <ChoiceField
              label={t("Query type")}
              value={query.kind}
              options={[
                { value: "metric", label: t("Environment / Device metric") },
                { value: "promql", label: "PromQL" },
              ]}
              onValueChange={(value) =>
                queryChange(
                  index,
                  value === "metric"
                    ? {
                        kind: "metric",
                        metric: metrics[0],
                        history: "configured",
                      }
                    : {
                        kind: "promql",
                        source_id:
                          sources.data?.find((source) => source.config.query)
                            ?.id ?? "",
                        expression: "",
                      },
                )
              }
            />
            {query.kind === "metric" ? (
              <>
                <ChoiceField
                  label={t("Metric")}
                  value={query.metric}
                  options={metrics.map((value) => ({
                    value,
                    label: metricLabel(value),
                  }))}
                  onValueChange={(metric) =>
                    queryChange(index, {
                      ...query,
                      metric: metrics.find((value) => value === metric)!,
                    })
                  }
                />
                <ChoiceField
                  label={t("History")}
                  value={query.history ?? "configured"}
                  options={[
                    { value: "configured", label: t("Configured data source") },
                    { value: "temporary", label: t("Temporary history") },
                  ]}
                  onValueChange={(history) =>
                    queryChange(index, {
                      ...query,
                      history:
                        history === "temporary" ? "temporary" : "configured",
                    })
                  }
                />
              </>
            ) : (
              <>
                <ChoiceField
                  label={t("Data source")}
                  value={query.source_id}
                  options={[
                    ...(query.source_id &&
                    !sources.data?.some(
                      (source) => source.id === query.source_id,
                    )
                      ? [
                          {
                            value: query.source_id,
                            label: `${t("Unavailable data source")} · ${query.source_id}`,
                            disabled: true,
                          },
                        ]
                      : []),
                    ...(sources.data ?? [])
                      .filter(
                        (source) =>
                          source.config.query || source.id === query.source_id,
                      )
                      .map((source) => ({
                        value: source.id,
                        label: source.name,
                        disabled: !source.enabled || !source.config.query,
                      })),
                  ]}
                  onValueChange={(source_id) =>
                    queryChange(index, { ...query, source_id })
                  }
                />
                <PromQLEditor
                  label={`${t("PromQL expression")} ${index + 1}`}
                  value={query.expression}
                  onChange={(expression) =>
                    queryChange(index, { ...query, expression })
                  }
                />
                {!can("admin") && (
                  <p>
                    {t(
                      "Workspace administrator access is required for PromQL queries.",
                    )}
                  </p>
                )}
              </>
            )}
          </div>
        ))}
        <div className={styles.row}>
          <Button
            type="button"
            variant="outline"
            disabled={draft.queries.length >= 4}
            onClick={() =>
              update({
                queries: [
                  ...draft.queries,
                  { kind: "metric", metric: metrics[0] },
                ],
              })
            }
          >
            {t("Add query")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!valid}
            onClick={() => {
              setPreview(structuredClone(draft));
              setRevision((value) => value + 1);
            }}
          >
            {t("Run queries")}
          </Button>
        </div>
        {preview && (
          <div className={styles.preview}>
            <MetricsPanel
              panel={preview}
              target={target}
              range={range}
              revision={revision}
              syncKey="panel-preview"
            />
          </div>
        )}
        <FormActions
          onCancel={onClose}
          label={t("Apply panel")}
          pending={false}
          disabled={!valid}
        />
      </form>
    </ModalFrame>
  );
}
