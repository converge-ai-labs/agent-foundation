import {
  Button,
  ChoiceField,
  DisclosureSection,
  ModalFrame,
  Tabs,
  TabsList,
  TabsTab,
  TabsPanel,
} from "a13n-ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ApiError } from "@converge.ai/a13n";
import { ArrowRightIcon, ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Page, Timestamp } from "../../shared/feedback";
import { TraceContent, TraceJson } from "./content";
import { formatCost, observationCost } from "./cost";
import { ObservationIcon } from "./identity";
import { MetadataChips, AttributeValues } from "./metadata";
import { compareObservations, type ObservationSort } from "./sorting";
import { CopyableId } from "../../shared/copy";
import shared from "../../shared/shared.module.css";
import { observationRows } from "./timeline";
import { Duration, durationMs, Level, TelemetryStatus } from "./values";
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
  const [order, setOrder] = useState("tree");
  const sourceHintId = useId();
  const [searchParams] = useSearchParams();
  const viewControl = (
    <ChoiceField
      label={t("Observation content")}
      className={styles.viewControl}
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
  if (!query.data) return <Loading variant="detail" page />;
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
  const cost = observationCost(observations);
  const costsComplete =
    observationsQuery.isSuccess &&
    !observationsQuery.hasNextPage &&
    !observationsQuery.error;
  const start = Math.min(
    ...observations.map((item) => Date.parse(item.started_at)),
  );
  const latest = Math.max(
    ...observations.map((item) => Date.parse(item.ended_at ?? item.started_at)),
  );
  const duration = Math.max(1, latest - start);
  const source = safeSource(trace.source_url);
  const [field, direction] = order.split(":");
  const rows =
    order === "tree"
      ? observationRows(observations)
      : [...observations]
          .sort((a, b) =>
            compareObservations(a, b, { field, direction } as ObservationSort),
          )
          .map((observation) => ({ observation, depth: 0 }));
  return (
    <Page
      title={root.name}
      description={trace.id}
      className={styles.detailPage}
      back={`${basePath}/traces`}
      actions={
        <>
          <Button
            variant="outline"
            render={
              <Link
                to={`${basePath}/sessions/${correlation.session_id}/threads/${correlation.thread_id}/runs/${correlation.run_id}`}
              />
            }
          >
            {t("View run")}
            <ArrowRightIcon aria-hidden="true" />
          </Button>
          {source && (
            <Button
              variant="outline"
              render={
                <a
                  href={source}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={t("Opens in a new tab")}
                  aria-describedby={sourceHintId}
                />
              }
            >
              {t("View in {{provider}}", {
                provider:
                  trace.provider === "langfuse" ? "Langfuse" : "Logfire",
              })}
              <ArrowSquareOutIcon aria-hidden="true" />
            </Button>
          )}
          {source && (
            <span id={sourceHintId} className="sr-only">
              {t("Opens in a new tab")}
            </span>
          )}
        </>
      }
    >
      <MetadataChips observation={root} />
      <div className={styles.metrics}>
        <Metric label={t("Level")}>
          <Level level={root.level} />
        </Metric>
        <Metric label={t("Duration")}>
          <Duration observation={root} />
        </Metric>
        <Metric label={t(costsComplete ? "Cost" : "Loaded cost")}>
          <span title={cost.total ?? undefined}>{formatCost(cost.total)}</span>
        </Metric>
        <Metric label={t("Started")}>
          <Timestamp value={root.started_at} />
        </Metric>
      </div>
      <p className={styles.providerNote}>
        {t(
          "Level and duration describe the root. Cost sums each loaded observation once; missing costs are not estimated.",
        )}
      </p>
      <Tabs
        defaultValue={
          searchParams.get("tab") === "content" ? "content" : "observations"
        }
        className={styles.detailTabs}
      >
        <TabsList
          size="sm"
          className={`${styles.detailTabList} a13n-scrollbar`}
        >
          <TabsTab value="observations">{t("Observations")}</TabsTab>
          <TabsTab value="content">{t("Input and output")}</TabsTab>
          <TabsTab value="metadata">{t("Metadata")}</TabsTab>
        </TabsList>
        <TabsPanel value="observations">
          <div className={styles.observationToolbar}>
            <p className={shared.muted}>
              {t("Loaded observations")}: {observations.length} ·{" "}
              {trace.provider}
            </p>
            <div className={styles.observationControls}>
              <ChoiceField
                label={t("Observation order")}
                value={order}
                onValueChange={setOrder}
                options={[
                  { value: "tree", label: t("Call tree") },
                  { value: "started:asc", label: t("Started · oldest first") },
                  { value: "started:desc", label: t("Started · newest first") },
                  {
                    value: "duration:desc",
                    label: t("Duration · longest first"),
                  },
                  {
                    value: "duration:asc",
                    label: t("Duration · shortest first"),
                  },
                  { value: "cost:desc", label: t("Cost · highest first") },
                  { value: "cost:asc", label: t("Cost · lowest first") },
                ]}
              />
              {viewControl}
            </div>
          </div>
          <p className={styles.providerNote}>
            {t(
              "Ordering applies to loaded observations only. Call tree preserves parent relationships.",
            )}
          </p>
          <div className={styles.timeline}>
            <div className={styles.timelineHeader}>
              <strong>{t("Observations")}</strong>
              <div className={styles.timelineScale}>
                <span>0 s</span>
                <span>
                  {(duration / 1000).toLocaleString(undefined, {
                    maximumFractionDigits: 6,
                  })}{" "}
                  s
                </span>
              </div>
            </div>
            {rows.map(({ observation, depth }) => {
              const left = Math.max(
                0,
                Math.min(
                  100,
                  ((Date.parse(observation.started_at) - start) / duration) *
                    100,
                ),
              );
              const elapsed = durationMs(observation);
              const width =
                elapsed === null
                  ? null
                  : Math.max(
                      0.5,
                      Math.min(100 - left, (elapsed / duration) * 100),
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
                        <ObservationIcon observation={observation} />
                        <span>
                          {observation.name}
                          <small>
                            {observation.type}
                            {(observation.model?.response ??
                              observation.model?.requested) &&
                              ` · ${observation.model?.response ?? observation.model?.requested}`}
                            {observation.parent_id &&
                              !byId.has(observation.parent_id) &&
                              ` · ${t("Parent not loaded")}`}
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
                  size="lg"
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
          {observationsQuery.isPending && <Loading variant="list" rows={6} />}
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
            {t("Costs reported")}: {cost.reported} / {observations.length}.{" "}
            {!costsComplete &&
              t("Load all observation pages for the trace cost.")}{" "}
            {t(
              "Sampling, export, retention, and unreported costs can leave gaps.",
            )}
          </p>
        </TabsPanel>
        <TabsPanel value="content">
          {viewControl}
          <p className={styles.providerNote}>
            {t(
              "Content belongs to the root observation. Missing output is not reconstructed from child calls.",
            )}
          </p>
          <ContentPair observation={root} view={view} />
        </TabsPanel>
        <TabsPanel value="metadata">
          {viewControl}
          <ObservationMetadata observation={root} view={view} />
          <DisclosureSection title={<>{t("Correlation")}</>}>
            <dl className={styles.properties}>
              {Object.entries(correlation).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>
                    <CopyableId value={value} />
                  </dd>
                </div>
              ))}
            </dl>
          </DisclosureSection>
        </TabsPanel>
      </Tabs>
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
  return (
    <div className={shared.stack}>
      <div className={styles.metrics}>
        <Metric label={t("Type")}>
          <span className={styles.typeIdentity}>
            <ObservationIcon observation={observation} />
            {observation.type}
          </span>
        </Metric>
        <Metric label={t("Duration")}>
          <Duration observation={observation} />
        </Metric>
        <Metric label={t("Cost")}>{formatCost(observation.cost_usd)}</Metric>
      </div>
      <Tabs defaultValue="input">
        <TabsList aria-label={t("Observation")}>
          <TabsTab value="input">{t("Input")}</TabsTab>
          <TabsTab value="output">{t("Output")}</TabsTab>
          <TabsTab value="metadata">{t("Metadata")}</TabsTab>
        </TabsList>
        <TabsPanel value="input">
          <TraceContent
            content={observation.input}
            compact={view === "compact"}
          />
        </TabsPanel>
        <TabsPanel value="output">
          <TraceContent
            content={observation.output}
            compact={view === "compact"}
          />
        </TabsPanel>
        <TabsPanel value="metadata">
          <div className={shared.stack}>
            <div className={styles.identifier}>
              <CopyableId value={observation.id} />
              {observation.parent_id && (
                <span>
                  {t("Parent ID")}: <CopyableId value={observation.parent_id} />
                </span>
              )}
            </div>
            <p>
              <Timestamp value={observation.started_at} /> —{" "}
              <Timestamp value={observation.ended_at} />
            </p>
            {observation.model && (
              <div className={styles.modelIdentity}>
                <Metric label={t("Requested model")}>
                  {observation.model.requested ?? "-"}
                </Metric>
                <Metric label={t("Response model")}>
                  {observation.model.response ?? "-"}
                </Metric>
              </div>
            )}
            <MetadataChips observation={observation} />
            <ObservationMetadata observation={observation} view={view} />
          </div>
        </TabsPanel>
      </Tabs>
    </div>
  );
}

function ContentPair({
  observation,
  view,
}: {
  observation: Schema["Observation"];
  view: Schema["TraceView"];
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.contentPair}>
      {(["input", "output"] as const).map((key) => (
        <section key={key}>
          <h3>{t(key === "input" ? "Input" : "Output")}</h3>
          <TraceContent
            content={observation[key]}
            compact={view === "compact"}
          />
        </section>
      ))}
    </div>
  );
}

function ObservationMetadata({
  observation,
  view,
}: {
  observation: Schema["Observation"];
  view: Schema["TraceView"];
}) {
  const { t } = useTranslation();
  const sections = [
    ["usage", "Usage"],
    ["attributes", "Attributes"],
    ["resource_attributes", "Resource attributes"],
    ["scope", "Instrumentation scope"],
    ["events", "Events"],
    ["links", "Links"],
  ] as const;
  return (
    <div className={styles.payloads}>
      <div className={styles.diagnostics}>
        <h3>{t("Diagnostics")}</h3>
        <p className={styles.providerNote}>
          {t(
            "Telemetry status is the span's reported OpenTelemetry status, not the Run outcome. Unset and unavailable do not mean success.",
          )}
        </p>
        <div className={styles.diagnosticValues}>
          <Metric label={t("Telemetry status")}>
            <TelemetryStatus observation={observation} />
          </Metric>
          <Metric label={t("Level")}>
            <Level level={observation.level} />
          </Metric>
        </div>
      </div>
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
        <DisclosureSection key={key} title={<>{t(label)}</>}>
          {key === "attributes" || key === "resource_attributes" ? (
            <AttributeValues value={observation[key]} />
          ) : (
            <TraceJson value={observation[key]} />
          )}
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
