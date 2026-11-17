import {
  Button,
  ChoiceField,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page, useTabParam } from "../../shared/page";
import { RefreshButton } from "../../shared/time-range";
import { AnalyzeDialog } from "./analyze-dialog";
import { ActiveAnalyses, AnalysisHistory } from "./history";
import {
  assessmentLabels,
  categoryLabels,
  severityLabels,
  Severity,
  useScopeName,
} from "./shared";
import styles from "./findings.module.css";

export function FindingsPage() {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [tab] = useTabParam(["findings", "history"]);
  const [open, setOpen] = useState(
    search.has("trace") || search.has("analyze"),
  );
  const [category, setCategory] = useState("all");
  const [severity, setSeverity] = useState("all"),
    [assessment, setAssessment] = useState("all"),
    [state, setState] = useState("open");
  const { agentName } = useScopeName();
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
  function switchTab(next: string) {
    setSearch(
      (current) => {
        const params = new URLSearchParams(current);
        params.delete("analysis");
        if (next === "findings") params.delete("tab");
        else params.set("tab", next);
        return params;
      },
      { replace: true },
    );
  }
  function closeAnalysis(created?: Schema["Analysis"]) {
    setOpen(false);
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("analyze");
        if (next.has("trace")) {
          next.delete("trace");
          next.delete("agent");
        }
        if (created) {
          // The new analysis opens beside its history so progress is visible at once.
          next.set("tab", "history");
          next.set("analysis", created.id);
        }
        return next;
      },
      { replace: true },
    );
  }
  const canStart = can("run") && can("write");
  const start = canStart ? (
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
      <ActiveAnalyses />
      <Tabs
        value={tab}
        onValueChange={(value) => switchTab(String(value))}
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
                      label={t("Status")}
                      value={state}
                      options={[
                        { value: "open", label: t("Open") },
                        { value: "closed", label: t("Closed") },
                        { value: "all", label: t("All") },
                      ]}
                      onValueChange={setState}
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
                      onValueChange={setAssessment}
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
                      onValueChange={setSeverity}
                    />
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
                      onValueChange={setCategory}
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
                              <span className={styles.mobileOnly}>
                                {" · "}
                                {agentName(finding.agent_id)}
                              </span>
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
                        render: (finding) => (
                          <span
                            className={styles.assessment}
                            data-assessment={finding.assessment}
                          >
                            {t(assessmentLabels[finding.assessment])}
                          </span>
                        ),
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
        <TabsPanel value="history" className={styles.tabPanel}>
          {tab === "history" && (
            <AnalysisHistory
              onStart={canStart ? () => setOpen(true) : undefined}
            />
          )}
        </TabsPanel>
      </Tabs>
      {open && canStart && (
        <AnalyzeDialog
          traceId={search.get("trace") ?? undefined}
          onClose={() => closeAnalysis()}
          onCreated={(analysis) => closeAnalysis(analysis)}
        />
      )}
    </Page>
  );
}
