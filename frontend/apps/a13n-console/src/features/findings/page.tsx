import {
  Button,
  Checkbox,
  ChoiceField,
  DisclosureSection,
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
import { useEffect, useId, useState } from "react";
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
import { CopyButton, CopyableId, Identifier } from "../../shared/identity";
import { Page, Section, useTabParam } from "../../shared/page";
import { RefreshButton } from "../../shared/time-range";
import { FormSection, TextAreaField } from "../../shared/forms";
import { useTraceBackend } from "../traces/backend";
import styles from "./findings.module.css";
import { useMountedModal } from "../../shared/dialogs";

export const categoryLabels = {
  unclear_request: "Unclear request",
  instruction_issue: "Instruction issue",
  tool_design: "Tool design",
  tool_usage: "Tool usage",
  tool_execution: "Tool execution",
  answer_quality: "Answer quality",
  context_gap: "Context gap",
  workflow_issue: "Workflow issue",
  boundary_violation: "Boundary violation",
} satisfies Record<Schema["Finding"]["category"], string>;
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
    description: "Failed tools, invalid inputs, and missing resources.",
  },
  {
    key: "recovery",
    label: "Retries and recovery",
    description: "Repeated failures and unnecessary retries.",
  },
  {
    key: "answer",
    label: "Answer quality",
    description: "False success claims, missed requests, and repeated answers.",
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
  const [category, setCategory] = useState("all");
  const [severity, setSeverity] = useState("all"),
    [assessment, setAssessment] = useState("all"),
    [state, setState] = useState("open");
  const agents = useFindingAgents();
  const filters = {
    category:
      category === "all"
        ? undefined
        : (category as Schema["Finding"]["category"]),
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
        "Find issues in agent runs and review suggested improvements.",
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
                      label={t("Category")}
                      value={category}
                      options={[
                        { value: "all", label: t("All") },
                        ...Object.entries(categoryLabels).map(
                          ([value, label]) => ({ value, label: t(label) }),
                        ),
                      ]}
                      onValueChange={(value) => filter(setCategory, value)}
                    />
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
                  <RefreshButton
                    pending={query.isFetching}
                    onRefresh={() => void query.refetch()}
                  />
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
                          <>
                            <Link
                              to={`${basePath}/findings/${finding.id}`}
                              className={styles.title}
                              title={finding.title}
                            >
                              {finding.title}
                            </Link>
                            <span className={styles.hint}>
                              {t(categoryLabels[finding.category])}
                            </span>
                          </>
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
                      "Try another filter or analyze traces to look for issues.",
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
  const [detailsOpen, setDetailsOpen] = useState(false);
  function selectAnalysis(id: string) {
    setSelectedId(id);
    setDetailsOpen(true);
  }
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
            items={query.data.items}
            caption={t("Analysis history")}
            onRowActivate={(item) => selectAnalysis(item.id)}
            columns={[
              {
                label: t("Scope"),
                tone: "primary",
                render: (item) => (
                  <button
                    type="button"
                    className={styles.rowTitle}
                    onClick={() => selectAnalysis(item.id)}
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
          />
        ))
      )}
      <CollectionFooter>
        <Pagination page={page} next={query.data?.next_cursor} />
      </CollectionFooter>
      {selected && (
        <ModalFrame
          open={detailsOpen}
          onOpenChange={setDetailsOpen}
          onOpenChangeComplete={(open) => {
            if (!open) setSelectedId(null);
          }}
          title={t("Analysis details")}
          closeLabel={t("Close")}
          footer={
            <>
              <Button variant="outline" onClick={() => setDetailsOpen(false)}>
                {t("Close")}
              </Button>
              <Button render={<Link to={runPath(selected)} />}>
                {t("Open analysis run")}
              </Button>
            </>
          }
        >
          <div className={styles.analysisDetail}>
            <Section title={t("Analysis summary")}>
              <dl className={styles.facts}>
                <div>
                  <dt>{t("Status")}</dt>
                  <dd>
                    <StatePill
                      state={selected.run_status}
                      label={t(
                        runStatusLabels[selected.run_status] ??
                          selected.run_status,
                      )}
                    />
                  </dd>
                </div>
                <div>
                  <dt>{t("Created")}</dt>
                  <dd>
                    <Timestamp value={selected.created_at} />
                  </dd>
                </div>
                <div>
                  <dt>{t("Scope")}</dt>
                  <dd>
                    {selected.agent_id ? (
                      <Link
                        className={styles.link}
                        to={`${basePath}/agents/${selected.agent_id}`}
                      >
                        {agentName(selected.agent_id)}
                      </Link>
                    ) : (
                      agentName(null)
                    )}
                  </dd>
                </div>
                <div>
                  <dt>{t("Selected traces")}</dt>
                  <dd>{selected.selected_traces.length}</dd>
                </div>
                <div>
                  <dt>{t("Findings")}</dt>
                  <dd>{selected.finding_count}</dd>
                </div>
                <div>
                  <dt>{t("Evidence-cited traces")}</dt>
                  <dd>{selected.cited_trace_count}</dd>
                </div>
              </dl>
              {selected.selection.instructions && (
                <TextAreaField
                  readOnly
                  label={t("Requirements for this analysis")}
                  value={selected.selection.instructions}
                  onChange={() => {}}
                />
              )}
              <p className={styles.hint}>
                {t(
                  "Counts do not indicate full coverage. Open the analysis run for the full report.",
                )}
              </p>
              {selected.selection_truncated && (
                <p className={styles.hint}>
                  {t("Trace limit reached. More traces may be available.")}
                </p>
              )}
            </Section>
            <DisclosureSection
              title={t("Selected traces")}
              summary={selected.selected_traces.length}
            >
              <ul className={styles.traceList}>
                {selected.selected_traces.map(({ trace_id: trace }) => (
                  <li key={trace}>
                    <Link to={`${basePath}/traces/${trace}`}>
                      <Identifier value={trace} />
                    </Link>
                    <CopyButton
                      value={trace}
                      iconOnly
                      copyLabel={t("Copy trace ID")}
                    />
                  </li>
                ))}
              </ul>
            </DisclosureSection>
          </div>
        </ModalFrame>
      )}
    </>
  );
}

function AnalyzeDialog({
  traceId,
  onClose: onClosed,
  onCreated,
}: {
  traceId?: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const formId = useId();
  const [created, setCreated] = useState(false);
  const { close: onClose, modalProps } = useMountedModal(() => {
    if (created) onCreated();
    else onClosed();
  });
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    backend = useTraceBackend();
  const [days, setDays] = useState("1"),
    [instructions, setInstructions] = useState(""),
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
            instructions,
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
      setCreated(true);
      onClose();
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
      {...modalProps}
      title={t("Analyze traces")}
      closeLabel={t("Close")}
      description={t(
        "Find potential issues for your review. Your agents stay unchanged.",
      )}
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={mutation.isPending}
            onClick={onClose}
          >
            {t("Cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={disabled}>
            {mutation.isPending ? t("Starting…") : t("Start analysis")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          if (!disabled) mutation.mutate();
        }}
        className={styles.form}
      >
        <ErrorNotice error={backend.error ?? mutation.error} />
        {backend.isSuccess && !backend.data.type && (
          <p role="alert">{t("Connect a trace provider to start analysis.")}</p>
        )}
        {traceId ? (
          <div className={styles.selectedTrace}>
            {t("Selected trace")}
            <CopyableId value={traceId} />
          </div>
        ) : (
          <FormSection title={t("Scope")}>
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
            <FormField label={t("Trace limit")} description={t("1–20 traces")}>
              <Input
                type="number"
                min={1}
                max={20}
                value={limit}
                disabled={mutation.isPending}
                onChange={(event) => setLimit(event.target.value)}
              />
            </FormField>
          </FormSection>
        )}
        <fieldset className={styles.rules} disabled={mutation.isPending}>
          <legend>{t("Checks")}</legend>
          <div className={styles.ruleList}>
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
          </div>
        </fieldset>
        <TextAreaField
          label={t("Requirements for this analysis")}
          hint={t(
            "Optional. Applies only to this analysis; the Finding Agent configuration stays unchanged.",
          )}
          value={instructions}
          onChange={setInstructions}
          maxLength={8192}
          disabled={mutation.isPending}
          rows={4}
        />
        <DisclosureSection title={t("How analysis works")}>
          <ul className={styles.analysisNotes}>
            <li>
              {t(
                "Analysis uses the Finding Agent's selected default version and model.",
              )}
            </li>
            {!traceId && (
              <li>
                {t(
                  "Failure and recovery checks prioritize traces with errors. Findings still need your review.",
                )}
              </li>
            )}
            <li>
              {t(
                "Previous findings and reviews for the same agent version help avoid duplicates. Context may be shortened.",
              )}
            </li>
            {!traceId && (
              <li>
                {t(
                  "Each analysis scans up to 100 traces and selects up to 20.",
                )}
              </li>
            )}
          </ul>
        </DisclosureSection>
      </form>
    </ModalFrame>
  );
}
