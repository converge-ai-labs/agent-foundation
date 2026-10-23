import { Button, ChoiceField, DisclosureSection, useMediaQuery } from "a13n-ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../../service-client";
import { ArrowRightIcon, ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import {
  DetailHeader,
  DetailPage,
  Page,
  Panel,
  Section,
  useTabParam,
} from "../../shared/page";
import { AnalyzeTrace } from "../findings/analyze-trace";
import { TraceContent } from "./content";
import { formatCost } from "../../shared/cost";
import { backendName, useTraceBackend } from "./backend";
import { runPath, traceCorrelation } from "./correlation";
import { observationCost } from "./cost";
import { ObservationGlyph } from "./identity";
import { MetadataChips } from "./metadata";
import {
  ObservationDiagnostics,
  ObservationPanelBody,
  ObservationPayloads,
  ObservationTitle,
} from "./observation-panel";
import { ObservationTree } from "./observation-tree";
import { compareObservations, type ObservationSort } from "./sorting";
import { CopyableId, IconTile } from "../../shared/identity";
import { observationRows, type TimelineRow } from "./timeline";
import { overviewData, type Observation } from "./overview";
import { Duration, Fact, LevelPill } from "./values";
import styles from "./traces.module.css";

const TABS = ["observations", "content", "metadata"] as const;

export function TraceDetailPage() {
  const { traceId = "" } = useParams();
  return <TraceDetail key={traceId} traceId={traceId} />;
}

export function TraceDetail({ traceId }: { traceId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [order, setOrder] = useState("tree");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useTabParam(TABS);
  // Wide screens keep the inspector beside the timeline, so rows stay clickable.
  const wide = useMediaQuery("lg");
  const sourceHintId = useId();
  const backend = useTraceBackend();
  const path = { trace_id: traceId };
  const query = useQuery({
    queryKey: ["traces", workspace.id, traceId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/traces/{trace_id}", {
          params: { path },
          signal,
        })
        .then(data),
  });
  const observationsQuery = useInfiniteQuery({
    queryKey: ["trace-observations", workspace.id, traceId],
    initialPageParam: undefined as string | undefined,
    enabled: !query.isError,
    queryFn: ({ pageParam, signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/traces/{trace_id}/spans", {
          params: {
            path,
            query: { view: "overview", limit: 50, cursor: pageParam },
          },
          signal,
        })
        .then(overviewData),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const selectedQuery = useQuery({
    queryKey: ["trace-observation", workspace.id, traceId, selectedId],
    enabled:
      query.isSuccess &&
      tab === "observations" &&
      selectedId !== null &&
      selectedId !== query.data.id,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/traces/{trace_id}/spans/{span_id}", {
          params: { path: { ...path, span_id: selectedId! } },
          signal,
        })
        .then(data),
  });
  // Do not display cached content after an authorization/not-found response.
  const accessError = [observationsQuery.error, selectedQuery.error].find(
    (error) =>
      error instanceof ApiError && [401, 403, 404].includes(error.status),
  );
  if (query.error || accessError)
    return (
      <Page
        title={t("Trace")}
        description={traceId}
        back={`${basePath}/traces`}
        backLabel={t("Traces")}
      >
        <ErrorNotice
          error={query.error ?? accessError}
          retry={() => {
            void query.refetch();
            void observationsQuery.refetch();
            if (selectedId && selectedId !== query.data?.id)
              void selectedQuery.refetch();
          }}
        />
      </Page>
    );
  if (!query.data) return <Loading variant="detail" page />;
  const root = query.data,
    correlation = traceCorrelation(root);
  const byId = new Map<string, Observation>(
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
  const source = safeSource(root.source_url);
  const [field, direction] = order.split(":");
  const rows: TimelineRow[] =
    order === "tree"
      ? observationRows(observations)
      : [...observations]
          .sort((a, b) =>
            compareObservations(a, b, { field, direction } as ObservationSort),
          )
          .map((observation) => ({ observation, depth: 0, childCount: 0 }));
  const selected =
    tab === "observations" && selectedId ? byId.get(selectedId) : undefined;
  const inspector = selected && (
    <Panel
      open
      inline={wide}
      resizable={!wide}
      label={t("Observation")}
      title={<ObservationTitle observation={selected} />}
      onClose={() => setSelectedId(null)}
    >
      {selected.id === root.id ? (
        <ObservationPanelBody key={root.id} observation={root} />
      ) : selectedQuery.error ? (
        <ErrorNotice
          error={selectedQuery.error}
          retry={() => void selectedQuery.refetch()}
        />
      ) : selectedQuery.data ? (
        <ObservationPanelBody
          key={selected.id}
          observation={selectedQuery.data}
        />
      ) : (
        <Loading variant="detail" />
      )}
    </Panel>
  );
  const provider = backend.data?.type && backendName(backend.data.type);
  const run = runPath(basePath, correlation);
  return (
    <DetailPage
      back={`${basePath}/traces`}
      backLabel={t("Traces")}
      tab={tab}
      onTabChange={setTab}
      tabs={[
        { value: "observations", label: t("Observations") },
        { value: "content", label: t("Input & output") },
        { value: "metadata", label: t("Metadata") },
      ]}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <ObservationGlyph observation={root} size={19} />
            </IconTile>
          }
          name={root.name}
          resourceKey={root.trace_id}
          actions={
            <>
              <AnalyzeTrace runId={correlation.run_id} traceId={traceId} />
              {run && (
                <Button
                  variant="outline"
                  render={<Link to={`${run}?view=debug`} />}
                >
                  {t("View run")}
                  <ArrowRightIcon aria-hidden="true" />
                </Button>
              )}
              {source && provider && (
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
                  {t("View in {{provider}}", { provider })}
                  <ArrowSquareOutIcon aria-hidden="true" />
                </Button>
              )}
              {source && provider && (
                <span id={sourceHintId} className="sr-only">
                  {t("Opens in a new tab")}
                </span>
              )}
            </>
          }
        />
      }
    >
      <div className={styles.detailContent}>
        <div>
          <div className={styles.facts}>
            <dl className={styles.factCells}>
              <Fact label={t("Level")}>
                <LevelPill level={root.level} />
              </Fact>
              <Fact label={t("Duration")}>
                <Duration observation={root} />
              </Fact>
              <Fact label={t(costsComplete ? "Cost" : "Loaded cost")}>
                <span title={cost.total ?? undefined}>
                  {formatCost(cost.total)}
                </span>
              </Fact>
              <Fact label={t("Started")}>
                <Timestamp value={root.started_at} />
              </Fact>
            </dl>
            <MetadataChips observation={root} />
          </div>
          <p className={styles.factsNote}>
            {t(
              "Level and duration describe the root. Cost sums each loaded observation once; missing costs are not estimated.",
            )}
          </p>
        </div>
        {tab === "observations" && (
          <div className={wide && selected ? styles.inspecting : undefined}>
            <div>
              <div className={styles.observationToolbar}>
                <p className={styles.observationCount}>
                  {t("{{count}} observations loaded", {
                    count: observations.length,
                  })}
                </p>
                <div className={styles.observationControls}>
                  <ChoiceField
                    label={t("Order")}
                    variant="filter"
                    value={order}
                    onValueChange={setOrder}
                    options={[
                      { value: "tree", label: t("Call tree") },
                      {
                        value: "started:asc",
                        label: t("Started · oldest first"),
                      },
                      {
                        value: "started:desc",
                        label: t("Started · newest first"),
                      },
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
                </div>
              </div>
              <ObservationTree
                rows={rows}
                tree={order === "tree"}
                start={start}
                duration={duration}
                loaded={new Set(byId.keys())}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
              {observationsQuery.isPending && (
                <Loading variant="list" rows={6} />
              )}
              <ErrorNotice
                error={observationsQuery.error}
                retry={() =>
                  void (observationsQuery.isFetchNextPageError
                    ? observationsQuery.fetchNextPage()
                    : observationsQuery.refetch())
                }
              />
              <div className={styles.timelineFooter}>
                {observationsQuery.hasNextPage && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={observationsQuery.isFetching}
                    onClick={() => void observationsQuery.fetchNextPage()}
                  >
                    {t("Load more observations")}
                  </Button>
                )}
                <p className={styles.providerNote}>
                  {costsComplete
                    ? t(
                        "{{reported}} of {{count}} observations report a cost. Sampling, export, and retention can leave gaps.",
                        { reported: cost.reported, count: observations.length },
                      )
                    : t(
                        "{{reported}} of {{count}} loaded observations report a cost. Load the remaining pages for the trace cost and a complete order.",
                        { reported: cost.reported, count: observations.length },
                      )}
                </p>
              </div>
            </div>
            {wide && selected && (
              <div className={styles.inspector}>{inspector}</div>
            )}
          </div>
        )}
        {tab === "content" && (
          <div>
            <div className={styles.contentPair}>
              {(["input", "output"] as const).map((key) => (
                <Section
                  key={key}
                  title={t(key === "input" ? "Input" : "Output")}
                >
                  <TraceContent content={root[key]} />
                </Section>
              ))}
            </div>
            <p className={styles.providerNote}>
              {t(
                "Content belongs to the root observation. Missing output is not reconstructed from child calls.",
              )}
            </p>
          </div>
        )}
        {tab === "metadata" && (
          <div className={styles.payloads}>
            <ObservationDiagnostics observation={root} summarized />
            <ObservationPayloads observation={root} />
            <DisclosureSection title={<>{t("Correlation")}</>}>
              <dl className={styles.properties}>
                {Object.entries(correlation).map(
                  ([key, value]) =>
                    value && (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>
                          <CopyableId value={value} />
                        </dd>
                      </div>
                    ),
                )}
              </dl>
            </DisclosureSection>
          </div>
        )}
      </div>
      {!wide && inspector}
    </DetailPage>
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
