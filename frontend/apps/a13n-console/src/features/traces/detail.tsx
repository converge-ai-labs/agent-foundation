import { Button, ChoiceField, DisclosureSection } from "a13n-ui";
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
import { Duration, Fact, TracePill } from "./values";
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
    enabled: query.isSuccess,
    queryFn: ({ pageParam, signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/traces/{trace_id}/spans", {
          params: { path, query: { limit: 50, cursor: pageParam } },
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
        backLabel={t("Traces")}
      >
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
  const root = query.data,
    correlation = traceCorrelation(root);
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
  const selected = selectedId ? byId.get(selectedId) : undefined;
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
                <TracePill level={root.level} />
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
            {observationsQuery.isPending && <Loading variant="list" rows={6} />}
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
                {t("Costs reported")}: {cost.reported} / {observations.length}.{" "}
                {!costsComplete &&
                  t("Load all observation pages for the trace cost.")}{" "}
                {t(
                  "Sampling, export, retention, and unreported costs can leave gaps.",
                )}{" "}
                {t(
                  "Ordering applies to loaded observations only. Call tree preserves parent relationships.",
                )}
              </p>
            </div>
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
            <ObservationDiagnostics observation={root} />
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
      {selected && (
        <Panel
          open
          label={t("Observation")}
          title={<ObservationTitle observation={selected} />}
          onClose={() => setSelectedId(null)}
        >
          <ObservationPanelBody observation={selected} />
        </Panel>
      )}
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
