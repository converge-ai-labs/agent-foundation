import {
  Button,
  Checkbox,
  ChoiceField,
  FormField,
  Input,
  Label,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
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
import { Page, Section, useTabParam } from "../../shared/page";
import { useTraceBackend } from "../traces/backend";
import styles from "./findings.module.css";

export const severityLabels = {
  critical: "Critical",
  warning: "Warning",
  suggestion: "Suggestion",
};
export const assessmentLabels = {
  unreviewed: "Unreviewed",
  confirmed: "Confirmed",
  expected: "Expected behavior",
  insufficient: "Insufficient evidence",
  false_positive: "False positive",
};
const runStatusLabels: Record<string, string> = {
  accepted: "Accepted",
  running: "Running",
  waiting: "Waiting",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};
const rules = [
  {
    key: "execution",
    label: "Execution failures",
    description:
      "Tool configuration, invalid arguments, unavailable resources, and unclear task intent.",
  },
  {
    key: "recovery",
    label: "Retries and recovery",
    description:
      "Recurring failures, retry loops, and costly recovery. Successful recovery is considered.",
  },
  {
    key: "answer",
    label: "Answer quality",
    description:
      "Misleading completion claims, intent mismatches, and repeated answers across distinct inputs.",
  },
] as const;
export function useFindingAgents() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["agents", workspace.id, "finding-choices"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/agents", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
}
export function Severity({ finding }: { finding: Schema["Finding"] }) {
  const { t } = useTranslation();
  return (
    <span className={styles.severity} data-severity={finding.severity}>
      {t(severityLabels[finding.severity ?? "warning"])}
      {finding.severity === "critical" && finding.assessment !== "confirmed"
        ? ` · ${t("Unconfirmed")}`
        : ""}
    </span>
  );
}
export function FindingsPage() {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [tab, setTab] = useTabParam(["findings", "history"]);
  const [open, setOpen] = useState(search.has("trace"));
  const [severity, setSeverity] = useState("all"),
    [assessment, setAssessment] = useState("all"),
    [state, setState] = useState("open");
  const agents = useFindingAgents();
  const filters = {
    severity:
      severity === "all"
        ? undefined
        : (severity as Schema["Finding"]["severity"]),
    assessment:
      assessment === "all"
        ? undefined
        : (assessment as Schema["Finding"]["assessment"]),
    closed: state === "all" ? undefined : state === "closed",
  };
  const page = useCursor(filters);
  const query = useQuery({
    queryKey: ["findings", workspace.id, filters, page.cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/findings", {
          params: { query: { ...filters, limit: 25, cursor: page.cursor } },
          signal,
        })
        .then(data),
  });
  const agentName = (id: string | null) =>
    id
      ? (agents.data?.find((agent) => agent.id === id)?.name ?? id)
      : t("Workspace traces");
  function filter(set: (value: string) => void, value: string) {
    set(value);
  }
  function closeAnalysis(created = false) {
    setOpen(false);
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (next.has("trace")) {
          next.delete("trace");
          next.delete("agent");
        }
        if (created) next.set("tab", "history");
        return next;
      },
      { replace: true },
    );
  }
  const start =
    can("run") && can("write") ? (
      <Button onClick={() => setOpen(true)}>
        <MagnifyingGlassIcon aria-hidden="true" />
        {t("Analyze traces")}
      </Button>
    ) : undefined;
  return (
    <Page
      title={t("Findings")}
      description={t(
        "Review execution issues and turn evidence into changes with Composer.",
      )}
      actions={start}
    >
      <Tabs
        value={tab}
        onValueChange={(value) => setTab(String(value))}
        className={styles.tabs}
      >
        <TabsList variant="underline" aria-label={t("Findings views")}>
          <TabsTab value="findings">{t("Findings")}</TabsTab>
          <TabsTab value="history">{t("Analysis history")}</TabsTab>
        </TabsList>
        <TabsPanel value="findings" className={styles.tabPanel}>
          {tab === "findings" && (
            <>
              <Toolbar
                filters={
                  <>
                    <ChoiceField
                      variant="filter"
                      label={t("Severity")}
                      value={severity}
                      options={[
                        { value: "all", label: t("All") },
                        ...Object.entries(severityLabels).map(
                          ([value, label]) => ({
                            value,
                            label: t(label),
                          }),
                        ),
                      ]}
                      onValueChange={(value) => filter(setSeverity, value)}
                    />
                    <ChoiceField
                      variant="filter"
                      label={t("Assessment")}
                      value={assessment}
                      options={[
                        { value: "all", label: t("All") },
                        ...Object.entries(assessmentLabels).map(
                          ([value, label]) => ({
                            value,
                            label: t(label),
                          }),
                        ),
                      ]}
                      onValueChange={(value) => filter(setAssessment, value)}
                    />
                    <ChoiceField
                      variant="filter"
                      label={t("Status")}
                      value={state}
                      options={[
                        { value: "open", label: t("Open") },
                        { value: "closed", label: t("Closed") },
                        { value: "all", label: t("All") },
                      ]}
                      onValueChange={(value) => filter(setState, value)}
                    />
                  </>
                }
                trailing={
                  <Button variant="ghost" onClick={() => void query.refetch()}>
                    {t("Refresh")}
                  </Button>
                }
              />
              <ErrorNotice
                error={query.error}
                retry={() => void query.refetch()}
              />
              {query.isPending ? (
                <Loading variant="table" columns={5} />
              ) : (
                query.isSuccess &&
                (query.data.items.length ? (
                  <ResourceTable
                    className={styles.findingsTable}
                    items={query.data.items}
                    caption={t("Findings")}
                    onRowActivate={(finding) =>
                      navigate(`${basePath}/findings/${finding.id}`)
                    }
                    columns={[
                      {
                        label: t("Finding"),
                        dataColumn: "finding",
                        tone: "primary",
                        render: (finding) => (
                          <Link
                            to={`${basePath}/findings/${finding.id}`}
                            className={styles.title}
                            title={finding.title}
                          >
                            {finding.title}
                          </Link>
                        ),
                      },
                      {
                        label: t("Agent"),
                        dataColumn: "agent",
                        render: (finding) => (
                          <Link
                            className={styles.agentName}
                            title={agentName(finding.agent_id)}
                            to={`${basePath}/agents/${finding.agent_id}`}
                          >
                            {agentName(finding.agent_id)}
                          </Link>
                        ),
                      },
                      {
                        label: t("Severity"),
                        dataColumn: "severity",
                        render: (finding) => <Severity finding={finding} />,
                      },
                      {
                        label: t("Assessment"),
                        dataColumn: "assessment",
                        render: (finding) =>
                          t(assessmentLabels[finding.assessment]),
                      },
                      {
                        label: t("Updated"),
                        dataColumn: "updated",
                        render: (finding) => (
                          <Timestamp value={finding.updated_at} relative />
                        ),
                      },
                    ]}
                  />
                ) : (
                  <Empty
                    icon={<MagnifyingGlassIcon aria-hidden="true" />}
                    title={t("No findings in this view")}
                    description={t(
                      "No findings does not mean every trace was analyzed. Check the analysis run or start a bounded analysis.",
                    )}
                    action={start}
                  />
                ))
              )}
              <CollectionFooter>
                <Pagination page={page} next={query.data?.next_cursor} />
              </CollectionFooter>
            </>
          )}
        </TabsPanel>
        <TabsPanel value="history" keepMounted className={styles.tabPanel}>
          <AnalysisHistory agentName={agentName} />
        </TabsPanel>
      </Tabs>
      {open && (
        <AnalyzeDialog
          traceId={search.get("trace") ?? undefined}
          onClose={() => closeAnalysis()}
          onCreated={() => closeAnalysis(true)}
        />
      )}
    </Page>
  );
}
function AnalysisHistory({
  agentName,
}: {
  agentName: (id: string | null) => string;
}) {
  const cache = useQueryClient(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["finding-analyses", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/finding-analyses", {
          params: { query: { limit: 20, cursor: page.cursor } },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) =>
        ["accepted", "running"].includes(item.run_status),
      )
        ? 3000
        : false,
  });
  const progress = query.data?.items
    .map(
      (item) =>
        `${item.id}:${item.run_status}:${item.finding_count}:${item.cited_trace_count}`,
    )
    .join(";");
  useEffect(() => {
    if (progress)
      void cache.invalidateQueries({ queryKey: ["findings", workspace.id] });
  }, [progress, cache, workspace.id]);
  const selected = query.data?.items.find((item) => item.id === selectedId);
  const runPath = (item: Schema["Analysis"]) =>
    `${basePath}/sessions/${item.session_id}/threads/${item.thread_id}/runs/${item.run_id}`;
  return (
    <>
      <Toolbar
        trailing={
          <Button variant="ghost" onClick={() => void query.refetch()}>
            {t("Refresh")}
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={3} />
      ) : (
        query.isSuccess &&
        (query.data.items.length ? (
          <ResourceTable
            items={query.data.items}
            caption={t("Analysis history")}
            onRowActivate={(item) => setSelectedId(item.id)}
            columns={[
              {
                label: t("Scope"),
                tone: "primary",
                render: (item) => (
                  <button
                    type="button"
                    className={styles.rowTitle}
                    onClick={() => setSelectedId(item.id)}
                  >
                    {agentName(item.agent_id)}
                  </button>
                ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill
                    state={item.run_status}
                    label={t(
                      runStatusLabels[item.run_status] ?? item.run_status,
                    )}
                  />
                ),
              },
              {
                label: t("Created"),
                render: (item) => <Timestamp value={item.created_at} />,
              },
            ]}
          />
        ) : (
          <Empty
            icon={<MagnifyingGlassIcon aria-hidden="true" />}
            title={t("No analysis has been started yet.")}
            description={t(
              "Start an analysis to review selected traces with Finding Agent.",
            )}
          />
        ))
      )}
      <CollectionFooter>
        <Pagination page={page} next={query.data?.next_cursor} />
      </CollectionFooter>
      {selected && (
        <ModalFrame
          open
          onOpenChange={(open) => {
            if (!open) setSelectedId(null);
          }}
          title={t("Analysis details")}
          description={agentName(selected.agent_id)}
          closeLabel={t("Close")}
        >
          <div className={styles.analysisDetail}>
            <div className={styles.metadata}>
              <StatePill
                state={selected.run_status}
                label={t(
                  runStatusLabels[selected.run_status] ?? selected.run_status,
                )}
              />
              <Timestamp value={selected.created_at} />
              {selected.agent_id && (
                <Link to={`${basePath}/agents/${selected.agent_id}`}>
                  {t("Agent")}
                </Link>
              )}
            </div>
            <Section title={t("Analysis summary")}>
              <p>
                {t("Selected traces: {{count}}", {
                  count: selected.selected_traces.length,
                })}
              </p>
              <p>
                {t("Findings: {{count}}", { count: selected.finding_count })}
              </p>
              <p>
                {t("Evidence-cited traces: {{count}}", {
                  count: selected.cited_trace_count,
                })}
              </p>
              <p className={styles.hint}>
                {t(
                  "Evidence citations do not show how many traces were fully analyzed. See the analysis run's final reply for results and limitations.",
                )}
              </p>
              {selected.selection_truncated && (
                <p className={styles.hint}>
                  {t("Selection capped; more traces may exist")}
                </p>
              )}
            </Section>
            <Section title={t("Traces")}>
              <div className={styles.links}>
                {selected.selected_traces.map(({ trace_id: trace }) => (
                  <Link key={trace} to={`${basePath}/traces/${trace}`}>
                    {trace}
                  </Link>
                ))}
              </div>
            </Section>
            <div className={styles.actions}>
              <Button variant="outline" onClick={() => setSelectedId(null)}>
                {t("Close")}
              </Button>
              <Button render={<Link to={runPath(selected)} />}>
                {t("Open analysis run")}
              </Button>
            </div>
          </div>
        </ModalFrame>
      )}
    </>
  );
}

function AnalyzeDialog({
  traceId,
  onClose,
  onCreated,
}: {
  traceId?: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    backend = useTraceBackend();
  const [days, setDays] = useState("1"),
    [limit, setLimit] = useState("10"),
    [presets, setPresets] = useState<Schema["AnalysisCreate"]["presets"]>([
      "execution",
      "recovery",
      "answer",
    ]);
  const [requestKey] = useState(() => crypto.randomUUID());
  const [windowEnd] = useState(() => new Date().toISOString());
  const mutation = useMutation({
    mutationFn: async () => {
      await client
        .workspace(workspace.id)
        .POST("/api/v1/finding-agent")
        .then(data);
      return client
        .workspace(workspace.id)
        .POST("/api/v1/finding-analyses", {
          body: {
            trace_id: traceId,
            started_after: traceId
              ? undefined
              : new Date(
                  Date.parse(windowEnd) - Number(days) * 86400000,
                ).toISOString(),
            started_before: traceId ? undefined : windowEnd,
            max_traces: traceId ? 1 : Number(limit),
            presets,
          },
          params: { header: commandHeaders(requestKey) },
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["finding-analyses", workspace.id],
      });
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      onCreated();
    },
  });
  const disabled =
    mutation.isPending ||
    !presets?.length ||
    !Number.isInteger(Number(limit)) ||
    Number(limit) < 1 ||
    Number(limit) > 20 ||
    !backend.data?.type;
  return (
    <ModalFrame
      open
      onOpenChange={(value) => {
        if (!value && !mutation.isPending) onClose();
      }}
      title={t("Analyze traces")}
      closeLabel={t("Close")}
      description={t(
        "Finding Agent reviews bounded execution evidence and submits unconfirmed findings.",
      )}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!disabled) mutation.mutate();
        }}
        className={styles.form}
      >
        <ErrorNotice error={backend.error ?? mutation.error} />
        <p className={styles.hint}>
          {t(
            "Analysis uses the built-in Finding Agent and automatically selects an available model.",
          )}
        </p>
        {backend.isSuccess && !backend.data.type && (
          <p role="alert">
            {t(
              "Configure a trace query provider before starting an analysis. Existing findings remain available.",
            )}
          </p>
        )}
        {traceId ? (
          <p className={styles.hint}>
            {t("Selected trace")}: {traceId}
          </p>
        ) : (
          <div className={styles.scope}>
            <ChoiceField
              label={t("Time range")}
              value={days}
              options={[
                { value: "1", label: t("Last 24 hours") },
                { value: "7", label: t("Last 7 days") },
                { value: "30", label: t("Last 30 days") },
              ]}
              onValueChange={setDays}
              disabled={mutation.isPending}
            />
            <FormField label={t("Maximum traces")}>
              <Input
                type="number"
                min={1}
                max={20}
                value={limit}
                disabled={mutation.isPending}
                onChange={(event) => setLimit(event.target.value)}
              />
            </FormField>
          </div>
        )}
        {!traceId && (
          <p className={styles.hint}>
            {t(
              "Execution and recovery rules prioritize failed Runs and error traces within the bounded scan. Other traces may still be selected; a signal does not prove a defect.",
            )}
          </p>
        )}
        <p className={styles.hint}>
          {t(
            "Existing findings from the exact selected Agent versions help avoid duplicate diagnoses. Reviewed findings also provide your feedback. This context may be truncated.",
          )}
        </p>
        <fieldset className={styles.rules} disabled={mutation.isPending}>
          <legend>{t("Built-in rules")}</legend>
          {rules.map((rule) => (
            <Label key={rule.key} className={styles.rule}>
              <Checkbox
                checked={presets?.includes(rule.key) ?? false}
                onCheckedChange={(checked) =>
                  setPresets((previous) =>
                    checked
                      ? [...(previous ?? []), rule.key]
                      : previous?.filter((key) => key !== rule.key),
                  )
                }
              />
              <span>
                {t(rule.label)}
                <span className={styles.hint}>{t(rule.description)}</span>
              </span>
            </Label>
          ))}
        </fieldset>
        <p className={styles.hint}>
          {t(
            "Analysis runs on demand using ordinary Service execution. At most 100 trace roots are scanned; selected traces are capped at 20. No changes are applied to the target Agent.",
          )}
        </p>
        <div className={styles.actions}>
          <Button
            type="button"
            variant="outline"
            disabled={mutation.isPending}
            onClick={onClose}
          >
            {t("Cancel")}
          </Button>
          <Button type="submit" disabled={disabled}>
            {mutation.isPending ? t("Starting…") : t("Start analysis")}
          </Button>
        </div>
      </form>
    </ModalFrame>
  );
}
