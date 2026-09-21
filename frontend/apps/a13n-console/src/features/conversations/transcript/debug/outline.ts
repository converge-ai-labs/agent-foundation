import { useQueries } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../../auth/context";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { agentQuery } from "../../../agents/queries";
import { runPath } from "../../api";
import {
  useChildThreads,
  useLineageThreads,
  useThreadRuns,
  type ThreadRuns,
} from "../thread-runs";
import { runSection } from "./view";

type Run = Schema["RunResource"];
type Thread = Schema["ThreadResource"];
type Translate = (key: string, options?: Record<string, unknown>) => string;

/** One Run, and the Threads that branched from it. */
export interface OutlineRun {
  run: Run;
  thread: Thread;
  label: string;
  groups: OutlineGroup[];
}

/** One Thread's Runs under the Run its Thread branched from. */
export interface OutlineGroup {
  thread: Thread;
  label: string;
  runs: OutlineRun[];
}

export interface RunOutline {
  /** The tree: normally the one Thread every other Thread descends from. */
  groups: OutlineGroup[];
  /** The same Runs in reading order, which is the tree read depth first. */
  sequence: readonly Run[];
  inView: string;
  /** The Run in view, named by its place. */
  label: string;
  open(run: Run): void;
}

/**
 * Every Run this Session reaches from here, as one tree: the Thread this one
 * branched from at the root, and under each Run the Threads it started — a
 * delegation, or a fork that reads on from it. Both the outline and the
 * pinned pill render this; neither decides what the tree is.
 */
export function useRunOutline(thread: Thread, runId: string): RunOutline {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const navigate = useNavigate();
  const { runs } = useThreadRuns(thread.id);
  const origins = useLineageThreads(runId, thread.id);
  const children = useChildThreads(thread.session_id, thread.id);
  const inView = useVisibleRun(runId);
  const branches: ThreadRuns[] = [...origins, { thread, runs }, ...children];
  const agentName = useAgentNames(branches);
  const groups = outlineGroups(branches, agentName, t);
  const found = locate(groups, inView);
  return {
    groups,
    sequence: groups.flatMap(readingOrder),
    inView,
    label: !found
      ? t("Runs")
      : found.group.thread.id !== thread.id
        ? `${found.group.label} · ${found.node.label}`
        : found.group.runs.length > 1
          ? t("Run {{index}} of {{total}}", {
              index: found.group.runs.indexOf(found.node) + 1,
              total: found.group.runs.length,
            })
          : found.node.label,
    open(run) {
      // A section already on the page is reached by scrolling, not by navigating.
      const loaded = runSection(run.id);
      if (loaded) loaded.scrollIntoView({ block: "start", behavior: "smooth" });
      else
        navigate(
          runPath(
            basePath,
            {
              session_id: run.session_id,
              thread_id: run.thread_id,
              run_id: run.id,
            },
            "debug",
          ),
        );
    },
  };
}

/** Each Thread under the Run it branched from; one Thread has no origin. */
function outlineGroups(
  branches: readonly ThreadRuns[],
  agentName: (agentId: string) => string | undefined,
  t: Translate,
): OutlineGroup[] {
  const groups = new Map<string, OutlineGroup>(
    branches.map((branch) => [
      branch.thread.id,
      {
        thread: branch.thread,
        label: "",
        runs: branch.runs.map((run, index) => ({
          run,
          thread: branch.thread,
          label: t("Run {{index}}", { index: index + 1 }),
          groups: [],
        })),
      },
    ]),
  );
  const roots: OutlineGroup[] = [];
  for (const branch of branches) {
    const group = groups.get(branch.thread.id)!;
    const origin = branch.thread.origin_thread_id
      ? groups.get(branch.thread.origin_thread_id)
      : undefined;
    // The Run a Thread names as its origin, or its origin Thread's last Run
    // when that Run is not among the ones this page can reach.
    const under =
      origin?.runs.find(
        (node) => node.run.id === branch.thread.origin_run_id,
      ) ?? origin?.runs.at(-1);
    group.label = groupLabel(branch, under?.run.agent_id, agentName, t);
    if (under) under.groups.push(group);
    else roots.push(group);
  }
  return roots;
}

function groupLabel(
  branch: ThreadRuns,
  parentAgentId: string | undefined,
  agentName: (agentId: string) => string | undefined,
  t: Translate,
) {
  if (branch.thread.role === "root") return t("Root thread");
  if (branch.thread.origin_kind === "fork") return t("Fork");
  const agentId = branch.runs[0]?.agent_id;
  const named = agentId && agentId !== parentAgentId && agentName(agentId);
  return named || t("Child thread");
}

const readingOrder = (group: OutlineGroup): Run[] =>
  group.runs.flatMap((node) => [
    node.run,
    ...node.groups.flatMap(readingOrder),
  ]);

function locate(
  groups: readonly OutlineGroup[],
  runId: string,
): { group: OutlineGroup; node: OutlineRun } | null {
  for (const group of groups)
    for (const node of group.runs) {
      if (node.run.id === runId) return { group, node };
      const nested = locate(node.groups, runId);
      if (nested) return nested;
    }
  return null;
}

/** The Agent answering each Thread, which is how a child Thread is named. */
function useAgentNames(branches: readonly ThreadRuns[]) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const ids = [
    ...new Set(
      branches
        .map((branch) => branch.runs[0]?.agent_id)
        .filter((id): id is string => !!id),
    ),
  ];
  const names = useQueries({
    queries: ids.map((id) => agentQuery(client, workspace.id, id)),
    combine: (results) =>
      new Map(results.map((result, index) => [ids[index], result.data?.name])),
  });
  return (agentId: string) => names.get(agentId);
}

/** Which loaded Run section the reader is actually looking at. */
function useVisibleRun(fallback: string) {
  const [visible, setVisible] = useState<string | null>(null);
  useEffect(() => {
    const stage = document.querySelector("[data-session-stage]");
    if (!(stage instanceof HTMLElement)) return;
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (records) => {
        for (const record of records) {
          const id = (record.target as HTMLElement).dataset.run;
          if (id) seen.set(id, record.intersectionRatio);
        }
        const sections = [...stage.querySelectorAll<HTMLElement>("[data-run]")];
        const top = sections.find(
          (section) => (seen.get(section.dataset.run ?? "") ?? 0) > 0,
        );
        if (top?.dataset.run) setVisible(top.dataset.run);
      },
      { root: stage, threshold: [0, 0.01, 0.5] },
    );
    const attach = () => {
      for (const section of stage.querySelectorAll("[data-run]"))
        observer.observe(section);
    };
    attach();
    const mutations = new MutationObserver(attach);
    mutations.observe(stage, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      mutations.disconnect();
    };
  }, []);
  return visible ?? fallback;
}
