import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { runPath } from "../../api";
import { useThreadRuns } from "../thread-runs";
import { runSection } from "./view";

type Run = Schema["RunListItem"];
type Thread = Schema["ThreadView"];

export interface OutlineRun {
  run: Run;
  thread: Thread;
  label: string;
}

export interface RunOutline {
  nodes: OutlineRun[];
  inView: string;
  label: string;
  total: number;
  loading: boolean;
  error: unknown;
  retry(): void;
  hasOlder: boolean;
  hasNewer: boolean;
  loadOlder(): void;
  loadNewer(): void;
  open(run: Run): void;
}

/** Both navigation presentations list only the current Thread's Runs. */
export function useRunOutline(thread: Thread, runId: string): RunOutline {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const navigate = useNavigate();
  const [, setSearch] = useSearchParams();
  const pages = useThreadRuns(thread.id, runId);
  const { runs, number } = pages;
  const visible = useVisibleRun(runId);
  // Inherited history can be visible above this branch; it is not a navigation target.
  const inView = runs.some((run) => run.id === visible) ? visible : runId;
  const position = number(inView);
  return {
    ...pages,
    nodes: runs.map((run) => ({
      run,
      thread,
      label: t("Run {{index}}", { index: number(run.id) }),
    })),
    inView,
    label:
      position === null
        ? t("Runs")
        : t("Run {{index}} of {{total}}", {
            index: position,
            total: pages.total,
          }),
    open(run) {
      const loaded = runSection(run.id);
      if (loaded && !loaded.closest("[inert]")) {
        setSearch(
          (params) => {
            params.delete("follow");
            return params;
          },
          { replace: true },
        );
        loaded.scrollIntoView({ block: "start", behavior: "smooth" });
      } else
        navigate(
          runPath(
            basePath,
            {
              session_id: run.session_id,
              thread_id: thread.id,
              run_id: run.id,
            },
            "debug",
          ),
        );
    },
  };
}

/** Which loaded Run section the reader is actually looking at. */
function useVisibleRun(fallback: string) {
  const [visible, setVisible] = useState<{
    selection: string;
    run: string;
  } | null>(null);
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
          (section) =>
            !section.closest("[inert]") &&
            (seen.get(section.dataset.run ?? "") ?? 0) > 0,
        );
        setVisible(
          top?.dataset.run
            ? { selection: fallback, run: top.dataset.run }
            : null,
        );
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
  }, [fallback]);
  return visible?.selection === fallback ? visible.run : fallback;
}
