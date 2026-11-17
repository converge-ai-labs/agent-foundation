import { Alert, AlertDescription, AlertTitle, Button } from "a13n-ui";
import {
  ArrowRightIcon,
  InfoIcon,
  MagnifyingGlassIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { CopyButton, Identifier } from "../../shared/identity";
import { Panel } from "../../shared/page";
import { RefreshButton } from "../../shared/time-range";
import { SessionTraceCount } from "./session-scope";
import {
  isActive,
  rules,
  runPath,
  runStatusLabels,
  useAnalyses,
  useScopeName,
} from "./shared";
import styles from "./findings.module.css";

/** The `analysis` search parameter names the analysis open in the inspector. */
export function useSelectedAnalysis() {
  const [search, setSearch] = useSearchParams();
  return {
    selectedId: search.get("analysis"),
    select: (id: string | null) =>
      setSearch((current) => {
        const next = new URLSearchParams(current);
        if (id) {
          next.set("tab", "history");
          next.set("analysis", id);
        } else next.delete("analysis");
        return next;
      }),
  };
}

function AnalysisStatus({ analysis }: { analysis: Schema["Analysis"] }) {
  const { t } = useTranslation();
  return (
    <StatePill
      state={analysis.run_status}
      label={t(runStatusLabels[analysis.run_status] ?? analysis.run_status)}
    />
  );
}

/** What was requested, without counts: a trace, a window, or every session trace. */
function RequestSummary({ analysis }: { analysis: Schema["Analysis"] }) {
  const { t } = useTranslation();
  const { selection } = analysis;
  const checks = selection.presets?.length ?? rules.length;
  return (
    <span className={styles.hint}>
      {selection.trace_id ? (
        t("Single trace")
      ) : selection.started_after && selection.started_before ? (
        <>
          <Timestamp value={selection.started_after} /> –{" "}
          <Timestamp value={selection.started_before} />
        </>
      ) : (
        t("All available session traces")
      )}
      {" · "}
      {t("{{checks}} of 3 checks", { checks })}
    </span>
  );
}

/** Active analyses stay visible above the findings they are still producing. */
export function ActiveAnalyses() {
  const { t } = useTranslation(),
    { basePath } = useWorkspace(),
    { scopeName } = useScopeName(),
    { select } = useSelectedAnalysis(),
    query = useAnalyses(undefined);
  const active = query.data?.items.filter(isActive) ?? [];
  if (!active.length) return null;
  const [first] = active;
  return (
    <div className={styles.activity} role="status">
      <span className={styles.pulse} aria-hidden="true" />
      <div className={styles.activityText}>
        <strong>
          {active.length === 1
            ? t("Analysis in progress")
            : t("{{count}} analyses in progress", { count: active.length })}
        </strong>
        <span>
          {active.length === 1 ? (
            <>
              {scopeName(first)} ·{" "}
              <Timestamp value={first.created_at} relative />
            </>
          ) : (
            t("New findings appear here as they are published.")
          )}
        </span>
      </div>
      <div className={styles.activityActions}>
        {active.length === 1 && (
          <Button
            variant="ghost"
            size="sm"
            render={<Link to={runPath(basePath, first)} />}
          >
            {t("Open run")}
          </Button>
        )}
        {active.length === 1 ? (
          <Button variant="outline" size="sm" onClick={() => select(first.id)}>
            {t("View progress")}
            <ArrowRightIcon aria-hidden="true" />
          </Button>
        ) : (
          <Button
            variant="outline"
            size="sm"
            render={<Link to={`${basePath}/findings?tab=history`} />}
          >
            {t("Analysis history")}
            <ArrowRightIcon aria-hidden="true" />
          </Button>
        )}
      </div>
    </div>
  );
}

export function AnalysisHistory({ onStart }: { onStart?: () => void }) {
  const { t } = useTranslation(),
    page = useCursor(),
    { scopeName } = useScopeName(),
    { selectedId, select } = useSelectedAnalysis(),
    query = useAnalyses(page.cursor);
  const selected = query.data?.items.find((item) => item.id === selectedId);
  return (
    <>
      <Toolbar
        trailing={
          <RefreshButton
            pending={query.isFetching}
            onRefresh={() => void query.refetch()}
          />
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={3} />
      ) : (
        query.isSuccess &&
        (query.data.items.length ? (
          <ResourceTable
            className={styles.historyTable}
            items={query.data.items}
            caption={t("Analysis history")}
            onRowActivate={(item) => select(item.id)}
            columns={[
              {
                label: t("Scope"),
                dataColumn: "scope",
                tone: "primary",
                render: (item) => (
                  <>
                    <button
                      type="button"
                      title={scopeName(item)}
                      className={styles.rowTitle}
                      aria-current={item.id === selectedId || undefined}
                      onClick={() => select(item.id)}
                    >
                      {scopeName(item)}
                    </button>
                    <RequestSummary analysis={item} />
                  </>
                ),
              },
              {
                label: t("Status"),
                dataColumn: "status",
                render: (item) => <AnalysisStatus analysis={item} />,
              },
              {
                label: t("Created"),
                dataColumn: "created",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
            ]}
          />
        ) : (
          <Empty
            icon={<MagnifyingGlassIcon aria-hidden="true" />}
            title={t("No analyses yet")}
            description={t(
              "Analyze traces to find potential issues in agent runs.",
            )}
            action={
              onStart && (
                <Button onClick={onStart}>{t("Analyze traces")}</Button>
              )
            }
          />
        ))
      )}
      <CollectionFooter>
        <Pagination page={page} next={query.data?.next_cursor} />
      </CollectionFooter>
      {selected && (
        <AnalysisInspector analysis={selected} onClose={() => select(null)} />
      )}
    </>
  );
}

/**
 * Inspects one analysis beside the history it belongs to. Selected, read and
 * cited traces are separate facts; none of them measures review coverage.
 */
function AnalysisInspector({
  analysis,
  onClose,
}: {
  analysis: Schema["Analysis"];
  onClose: () => void;
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace(),
    { scopeName } = useScopeName();
  const { selection } = analysis;
  const active = isActive(analysis);
  const selectedIds = new Set(
    analysis.selected_traces.map((trace) => trace.trace_id),
  );
  const readIds = new Set(analysis.read_trace_ids);
  const traces = [
    ...analysis.selected_traces.map((trace) => trace.trace_id),
    ...analysis.read_trace_ids.filter((id) => !selectedIds.has(id)),
  ];
  const presets = new Set(selection.presets ?? rules.map((rule) => rule.key));
  return (
    <Panel
      open
      label={t("Analysis details")}
      defaultWidth={520}
      dismissOnOutsidePress
      onClose={onClose}
      title={<strong>{scopeName(analysis)}</strong>}
      actions={
        <Button size="sm" render={<Link to={runPath(basePath, analysis)} />}>
          {t("Open analysis run")}
        </Button>
      }
    >
      <div className={styles.inspector}>
        <dl className={styles.overview}>
          <div>
            <dt>{t("Status")}</dt>
            <dd>
              <AnalysisStatus analysis={analysis} />
            </dd>
          </div>
          <div>
            <dt>{t("Created")}</dt>
            <dd>
              <Timestamp value={analysis.created_at} />
            </dd>
          </div>
          <div>
            <dt>{t("Scope")}</dt>
            <dd>
              {analysis.agent_id && !selection.session_ids?.length ? (
                <Link
                  className={styles.link}
                  to={`${basePath}/agents/${analysis.agent_id}`}
                >
                  {scopeName(analysis)}
                </Link>
              ) : (
                scopeName(analysis)
              )}
            </dd>
          </div>
          <div>
            <dt>{t("Time range")}</dt>
            <dd>
              {selection.trace_id ? (
                t("Single trace")
              ) : selection.started_after && selection.started_before ? (
                <span>
                  <Timestamp value={selection.started_after} /> –{" "}
                  <Timestamp value={selection.started_before} />
                </span>
              ) : (
                t("All available session traces")
              )}
            </dd>
          </div>
          <div>
            <dt>{t("Checks")}</dt>
            <dd className={styles.checkList}>
              {rules
                .filter((rule) => presets.has(rule.key))
                .map((rule) => (
                  <span key={rule.key}>{t(rule.label)}</span>
                ))}
            </dd>
          </div>
        </dl>
        {active ? (
          <Alert variant="info">
            <InfoIcon aria-hidden="true" />
            <AlertTitle>{t("Analysis in progress")}</AlertTitle>
            <AlertDescription>
              <p>
                {t(
                  "Counts update as the analyst reads traces and publishes findings.",
                )}
              </p>
            </AlertDescription>
          </Alert>
        ) : (
          ["failed", "cancelled"].includes(analysis.run_status) && (
            <Alert>
              <InfoIcon aria-hidden="true" />
              <AlertTitle>{t("Analysis ended early")}</AlertTitle>
              <AlertDescription>
                <p>
                  {t(
                    "Published findings remain available. The analysis run explains what happened.",
                  )}
                </p>
              </AlertDescription>
            </Alert>
          )
        )}
        <section
          className={styles.inspectorSection}
          aria-labelledby={`${analysis.id}-evidence`}
        >
          <h3 id={`${analysis.id}-evidence`}>{t("Evidence")}</h3>
          <dl className={styles.figures}>
            <div>
              <dt>{t("Selected traces")}</dt>
              <dd>{analysis.selected_traces.length}</dd>
              <p>{t("Initial targets")}</p>
            </div>
            <div>
              <dt>{t("Read traces")}</dt>
              <dd>{analysis.read_trace_ids.length}</dd>
              <p>{t("Opened successfully")}</p>
            </div>
            <div>
              <dt>{t("Evidence-cited traces")}</dt>
              <dd>{analysis.cited_trace_count}</dd>
              <p>{t("Cited by findings")}</p>
            </div>
            <div>
              <dt>{t("Findings")}</dt>
              <dd>{analysis.finding_count}</dd>
              <p>{active ? t("Published so far") : t("Published")}</p>
            </div>
          </dl>
          <p className={styles.hint}>
            {t(
              "Counts do not indicate full coverage. Open the analysis run for the full report.",
            )}
          </p>
          {analysis.selection_truncated && (
            <p className={styles.hint}>
              {t("Trace limit reached. More traces may be available.")}
            </p>
          )}
        </section>
        {traces.length > 0 && (
          <section
            className={styles.inspectorSection}
            aria-labelledby={`${analysis.id}-traces`}
          >
            <h3 id={`${analysis.id}-traces`}>{t("Traces")}</h3>
            <ul className={styles.traceList}>
              {traces.map((trace) => (
                <li key={trace}>
                  <span className={styles.traceId}>
                    <Link to={`${basePath}/traces/${trace}`}>
                      <Identifier value={trace} />
                    </Link>
                    <CopyButton
                      value={trace}
                      iconOnly
                      copyLabel={t("Copy trace ID")}
                    />
                  </span>
                  <span className={styles.traceOrigin}>
                    {selectedIds.has(trace)
                      ? t("Selected")
                      : t("Found by analyst")}
                  </span>
                  <span
                    className={styles.traceRead}
                    data-read={readIds.has(trace) || undefined}
                  >
                    {readIds.has(trace)
                      ? t("trace.read")
                      : active
                        ? t("Not read yet")
                        : t("Not read")}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {!!selection.session_ids?.length && (
          <section
            className={styles.inspectorSection}
            aria-labelledby={`${analysis.id}-sessions`}
          >
            <h3 id={`${analysis.id}-sessions`}>{t("Requested sessions")}</h3>
            <ul className={styles.scopeSessions}>
              {selection.session_ids.map((id) => (
                <SessionTraceCount
                  key={id}
                  id={id}
                  count={
                    analysis.selected_traces.filter(
                      (trace) => trace.session_id === id,
                    ).length
                  }
                />
              ))}
            </ul>
          </section>
        )}
        {selection.instructions && (
          <TextAreaField
            readOnly
            label={t("Requirements for this analysis")}
            value={selection.instructions}
            onChange={() => {}}
          />
        )}
      </div>
    </Panel>
  );
}
