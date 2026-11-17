import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import styles from "./findings.module.css";

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
export const runStatusLabels: Record<string, string> = {
  accepted: "Accepted",
  running: "Running",
  waiting: "Waiting",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};
export const rules = [
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

/** Run states in which an analysis can still read traces and publish findings. */
export function isActive(analysis: Schema["Analysis"]) {
  return ["accepted", "running", "waiting"].includes(analysis.run_status);
}

export function runPath(basePath: string, analysis: Schema["Analysis"]) {
  return `${basePath}/sessions/${analysis.session_id}/threads/${analysis.thread_id}/runs/${analysis.run_id}`;
}

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

/**
 * One page of analysis history. Active analyses keep it refreshing, and their
 * progress refreshes the findings they publish, whichever view is visible.
 */
export function useAnalyses(cursor: string | undefined) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace();
  const query = useQuery({
    queryKey: ["finding-analyses", workspace.id, cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/finding-analyses", {
          params: { query: { limit: 20, cursor } },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.items.some(isActive) ? 3000 : false,
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
  return query;
}

/** The scope an operator recognizes: requested Sessions, the target Agent, or the workspace. */
export function useScopeName() {
  const { t } = useTranslation(),
    agents = useFindingAgents();
  const agentName = (id: string | null) =>
    id
      ? (agents.data?.find((agent) => agent.id === id)?.name ?? id)
      : t("Workspace traces");
  return {
    agentName,
    scopeName: (analysis: Schema["Analysis"]) =>
      analysis.selection.session_ids?.length
        ? t("Sessions ({{count}})", {
            count: analysis.selection.session_ids.length,
          })
        : agentName(analysis.agent_id),
  };
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
