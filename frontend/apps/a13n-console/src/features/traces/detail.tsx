import { Button, ChoiceField, DisclosureSection, ModalFrame } from "a13n-ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "@converge.ai/a13n";
import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Page, Timestamp } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import shared from "../../shared/shared.module.css";
import { observationRows } from "./timeline";
import { Duration, durationMs, Severity, TelemetryStatus } from "./values";
import styles from "./traces.module.css";

export function TraceDetailPage() {
  const { traceId = "" } = useParams();
  return <TraceDetail key={traceId} traceId={traceId} />;
}

export function TraceDetail({ traceId }: { traceId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [view, setView] = useState<Schema["TraceView"]>("full");
  const viewControl = (
    <ChoiceField
      label={t("Observation content")}
      value={view}
      onValueChange={(value) => {
        if (value === "compact" || value === "full") setView(value);
      }}
      options={[
        { value: "full", label: t("Full") },
        { value: "compact", label: t("Compact") },
      ]}
    />
  );
  const path = { workspace: workspace.id, trace_id: traceId };
  const query = useQuery({
    queryKey: ["traces", workspace.id, traceId, view],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/traces/{trace_id}", {
          params: { path, query: { view } },
          signal,
        })
        .then(data),
  });
  const observationsQuery = useInfiniteQuery({
    queryKey: ["trace-observations", workspace.id, traceId, view],
    initialPageParam: undefined as string | undefined,
    enabled: query.isSuccess,
    queryFn: ({ pageParam, signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/traces/{trace_id}/observations", {
          params: { path, query: { view, limit: 50, cursor: pageParam } },
          signal,
        })
        .then(data),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  // Do not display cached content after an authorization/not-found response.
  const accessError =
    observationsQuery.error instanceof ApiError &&
    [401, 403, 404].includes(observationsQuery.error.status)
      ? observationsQuery.error
      : undefined;
  if (query.error || accessError)
    return (
      <Page
        title={t("Trace")}
        description={traceId}
        back={`${basePath}/traces`}
      >
        {viewControl}
        <ErrorNotice
          error={query.error ?? accessError}
          retry={() => {
            void query.refetch();
            void observationsQuery.refetch();
          }}
        />
      </Page>
    );
  if (!query.data) return <Loading />;
  const trace = query.data,
    root = trace.root,
    correlation = trace.correlation;
  const byId = new Map(
    observationsQuery.data?.pages
      .flatMap((page) => page.items)
      .map((item) => [item.id, item]),
  );
  byId.set(root.id, root);
  const observations = [...byId.values()];
  const start = Math.min(
    ...observations.map((item) => Date.parse(item.started_at)),
  );
  const latest = Math.max(
    ...observations.map((item) => Date.parse(item.ended_at ?? item.started_at)),
  );
  const duration = Math.max(1, latest - start);
  const source = safeSource(trace.source_url);
  return (
    <Page
      title={root.name}
      description={trace.id}
      back={`${basePath}/traces`}
      actions={
        <>
          <Link
            to={`${basePath}/sessions/${correlation.session_id}/threads/${correlation.thread_id}/runs/${correlation.run_id}`}
          >
            {t("Open run")}
          </Link>
          {source && (
            <a href={source} target="_blank" rel="noopener noreferrer">
              {t("Open trace backend")} <ArrowSquareOutIcon size={13} />
            </a>
          )}
        </>
      }
    >
      <div className={styles.metrics}>
        <Metric label={t("Root status")}>
          <TelemetryStatus observation={root} />
        </Metric>
        <Metric label={t("Severity")}>
          <Severity level={root.level} />
        </Metric>
        <Metric label={t("Root duration")}>
          <Duration observation={root} />
        </Metric>
        <Metric label={t("Root cost (USD)")}>
          {root.cost_usd ?? t("Unavailable")}
        </Metric>
        <Metric label={t("Started")}>
          <Timestamp value={root.started_at} />
        </Metric>
      </div>
      <p className={styles.providerNote}>
        {t(
          "Metrics describe the root observation, not trace totals. Open the run for execution outcome.",
        )}
      </p>
      <div className={styles.observationToolbar}>
        <p className={shared.muted}>
          {t("Loaded observations")}: {observations.length} · {trace.provider}
        </p>
        {viewControl}
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
          );
          const elapsed = durationMs(observation);
          const width =
            elapsed === null
              ? null
              : Math.max(0.5, Math.min(100 - left, (elapsed / duration) * 100));
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
                      data-level={observation.level}
                    />
                    <span>
                      {observation.name}
                      <small>
                        {observation.type}
                        {observation.model &&
                          ` · ${observation.model.response ?? observation.model.requested}`}
                      </small>
                    </span>
                  </span>
                  <span className={styles.track}>
                    {width !== null && (
                      <span
                        className={styles.bar}
                        style={{ left: `${left}%`, width: `${width}%` }}
                        data-status={observation.status}
                        data-level={observation.level}
                      />
                    )}
                    <span className={styles.duration}>
                      <Duration observation={observation} />
                    </span>
                  </span>
                </Button>
              }
              size="md"
              title={observation.name}
              description={t(
                "Telemetry content reflects the producer's content policy and backend retention.",
              )}
              closeLabel={t("Close")}
            >
              <ObservationDetails observation={observation} view={view} />
            </ModalFrame>
          );
        })}
      </div>
      {observationsQuery.isPending && <Loading />}
      <ErrorNotice
        error={observationsQuery.error}
        retry={() =>
          void (observationsQuery.isFetchNextPageError
            ? observationsQuery.fetchNextPage()
            : observationsQuery.refetch())
        }
      />
      {observationsQuery.hasNextPage && (
        <Button
          variant="outline"
          disabled={observationsQuery.isFetching}
          onClick={() => void observationsQuery.fetchNextPage()}
        >
          {t("Load more observations")}
        </Button>
      )}
      <p className={styles.providerNote}>
        {t(
          "Only loaded observations are shown. Sampling, export, and retention can leave gaps.",
        )}
      </p>
      <div className={styles.payloads}>
        {(["input", "output", "usage"] as const).map((key) => (
          <DisclosureSection
            key={key}
            title={
              <>
                {t(
                  key === "input"
                    ? "Root input"
                    : key === "output"
                      ? "Root output"
                      : "Root usage",
                )}
              </>
            }
          >
            <JsonView value={root[key]} />
          </DisclosureSection>
        ))}
        <DisclosureSection title={<>{t("Correlation")}</>}>
          <JsonView value={correlation} />
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
    <div className={styles.metric}>
      <small>{label}</small>
      <strong>{children}</strong>
    </div>
  );
}

function ObservationDetails({
  observation,
  view,
}: {
  observation: Schema["Observation"];
  view: Schema["TraceView"];
}) {
  const { t } = useTranslation();
  const sections = [
    ["input", "Input"],
    ["output", "Output"],
    ["usage", "Usage"],
    ["attributes", "Attributes"],
    ["resource_attributes", "Resource attributes"],
    ["scope", "Instrumentation scope"],
    ["events", "Events"],
    ["links", "Links"],
  ] as const;
  return (
    <div className={shared.stack}>
      <div className={styles.metrics}>
        <Metric label={t("Telemetry status")}>
          <TelemetryStatus observation={observation} />
        </Metric>
        <Metric label={t("Severity")}>
          <Severity level={observation.level} />
        </Metric>
        <Metric label={t("Type")}>{observation.type}</Metric>
        <Metric label={t("Cost (USD)")}>
          {observation.cost_usd ?? t("Unavailable")}
        </Metric>
      </div>
      <p className={styles.identifier}>
        {observation.id}
        {observation.parent_id && (
          <>
            {" "}
            · {t("Parent ID")}: {observation.parent_id}
          </>
        )}
      </p>
      <p>
        <Timestamp value={observation.started_at} /> —{" "}
        <Timestamp value={observation.ended_at} />
      </p>
      <Metric label={t("Requested model")}>
        {observation.model?.requested ?? t("Unavailable")}
      </Metric>
      <Metric label={t("Response model")}>
        {observation.model?.response ?? t("Unavailable")}
      </Metric>
      {view === "compact" && (
        <p className={shared.muted}>
          {t(
            "Switch to Full to read retained content and diagnostic attributes.",
          )}
        </p>
      )}
      {observation.status_message !== null && (
        <div>
          <small>{t("Status message")}</small>
          <pre className={styles.statusMessage}>
            {observation.status_message}
          </pre>
        </div>
      )}
      {sections.map(([key, label]) => (
        <DisclosureSection
          key={key}
          defaultOpen={key === "input" || key === "output"}
          title={<>{t(label)}</>}
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
