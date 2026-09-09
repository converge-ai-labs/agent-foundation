import { Button, DisclosureSection, ModalFrame } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import shared from "../../shared/shared.module.css";
import { observationRows } from "./timeline";
import styles from "./traces.module.css";

export function TraceDetailPage() {
  const { traceId = "" } = useParams();
  return <TraceDetail traceId={traceId} />;
}
export function TraceDetail({ traceId }: { traceId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["traces", workspace.id, traceId, "full"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/traces/{trace_id}", {
          params: {
            path: { workspace: workspace.id, trace_id: traceId },
            query: { view: "full" },
          },
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const { trace, observations } = query.data,
    start = Date.parse(trace.started_at),
    latest = Math.max(
      start,
      ...observations.map((item) =>
        Date.parse(item.ended_at ?? item.started_at),
      ),
    ),
    duration = Math.max(1, latest - start);
  const source = safeSource(trace.source_url);
  return (
    <Page
      title={trace.name}
      description={trace.id}
      back={`${basePath}/traces`}
      actions={
        <>
          <Link
            to={`${basePath}/sessions/${trace.session_id}/threads/${trace.thread_id}/runs/${trace.run_id}`}
          >
            {t("Open run")}
          </Link>
          {source && (
            <a href={source} target="_blank" rel="noopener noreferrer">
              {t("Open trace backend")} <ExternalLink size={13} />
            </a>
          )}
        </>
      }
    >
      <div className={styles.metrics}>
        <Metric label={t("Telemetry status")}>
          <StateBadge state={trace.trace_status} />
        </Metric>
        <Metric label={t("Attempt outcome")}>
          <StateBadge state={trace.run_attempt_outcome ?? "unavailable"} />
        </Metric>
        <Metric label={t("Duration")}>
          {trace.duration_ms === null
            ? t("Unavailable")
            : `${trace.duration_ms} ms`}
        </Metric>
        <Metric label={t("Cost (USD)")}>
          {trace.total_cost_usd ?? t("Unavailable")}
        </Metric>
        <Metric label={t("Started")}>
          <Timestamp value={trace.started_at} />
        </Metric>
      </div>
      <div className={styles.timeline}>
        <div className={styles.timelineHeader}>
          <strong>{t("Observations")}</strong>
          <small>{t("Duration")}</small>
        </div>
        {observationRows(observations).map(({ observation, depth }) => {
          const left = Math.max(
              0,
              Math.min(
                100,
                ((Date.parse(observation.started_at) - start) / duration) * 100,
              ),
            ),
            width =
              observation.duration_ms === null
                ? null
                : Math.max(
                    0.5,
                    Math.min(
                      100 - left,
                      (observation.duration_ms / duration) * 100,
                    ),
                  );
          return (
            <ModalFrame
              key={observation.id}
              trigger={
                <Button
                  className={styles.observation}
                  variant="ghost"
                  type="button"
                >
                  <span
                    className={styles.observationName}
                    style={{ paddingInlineStart: Math.min(depth, 12) * 14 }}
                  >
                    <span
                      className={styles.dot}
                      data-status={observation.status}
                    />
                    <span>
                      {observation.name}
                      <small>{observation.model ?? observation.type}</small>
                    </span>
                  </span>
                  <span className={styles.track}>
                    {width !== null && (
                      <span
                        className={styles.bar}
                        style={{ left: `${left}%`, width: `${width}%` }}
                        data-status={observation.status}
                      />
                    )}
                    <span className={styles.duration}>
                      {observation.duration_ms === null
                        ? t("Unavailable")
                        : `${observation.duration_ms} ms`}
                    </span>
                  </span>
                </Button>
              }
              size={"md"}
              title={observation.name}
              description={t(
                "Telemetry content reflects the producer's content policy and backend retention.",
              )}
              closeLabel={t("Close")}
            >
              <ObservationDetails observation={observation} />
            </ModalFrame>
          );
        })}
        {!observations.length && (
          <p className={shared.muted}>
            {t("No observations were retained for this trace.")}
          </p>
        )}
      </div>
      <div className={styles.payloads}>
        <DisclosureSection title={<>{t("Input")}</>}>
          <JsonView value={trace.input} />
        </DisclosureSection>
        <DisclosureSection title={<>{t("Output")}</>}>
          <JsonView value={trace.output} />
        </DisclosureSection>
        <DisclosureSection title={<>{t("Usage")}</>}>
          <JsonView value={trace.usage} />
        </DisclosureSection>
      </div>
    </Page>
  );
}
function Metric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <small>{label}</small>
      <strong>{children}</strong>
    </div>
  );
}
function ObservationDetails({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  const { t } = useTranslation();
  return (
    <div className={shared.stack}>
      <StateBadge state={observation.status} />
      <p>
        <Timestamp value={observation.started_at} /> —{" "}
        <Timestamp value={observation.ended_at} />
      </p>
      <p>{observation.model}</p>
      {(["input", "output", "metadata", "usage"] as const).map((key) => (
        <DisclosureSection
          key={key}
          defaultOpen={key === "input" || key === "output"}
          title={<>{t(key[0]!.toUpperCase() + key.slice(1))}</>}
        >
          <JsonView value={observation[key]} />
        </DisclosureSection>
      ))}
    </div>
  );
}
function safeSource(url?: string | null) {
  try {
    const parsed = new URL(url ?? "");
    return ["http:", "https:"].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}
