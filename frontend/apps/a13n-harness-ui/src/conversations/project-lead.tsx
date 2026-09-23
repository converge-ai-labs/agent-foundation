import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Button } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { refreshThreadLists, useThread } from "./queries";
import { ThreadRow } from "./thread-row";
import styles from "./project-lead.module.css";

import { LeadIcon } from "./lead-icon";

export function useProjectLeadMode(projectId?: string) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      result(
        client.PATCH("/api/projects/{project_id}/lead", {
          params: { path: { project_id: projectId! } },
          body: { enabled },
        }),
      ),
    onSuccess: (thread, enabled) => {
      if (enabled) navigate(`/threads/${encodeURIComponent(thread.thread_id)}`);
    },
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["projects"] });
      void refreshThreadLists(queries);
    },
    retry: false,
  });
}

export function ProjectLeadEntry({
  project,
  presence,
  enabled,
}: {
  project: Schema<"ProjectSummary">;
  presence: Schema<"PresenceFrame"> | null;
  enabled: boolean;
}) {
  const navigate = useNavigate();
  const lead = useThread(enabled ? (project.lead_thread_id ?? "") : "");
  if (project.lead_thread_id)
    return (
      <>
        {lead.data ? (
          <ThreadRow
            row={{
              thread: lead.data.thread,
              pending_decision: lead.data.deferred_requests?.length
                ? { kind: "mixed", count: lead.data.deferred_requests.length }
                : null,
            }}
            presence={presence}
            projectLead={project.lead_enabled}
            showRestore
          />
        ) : (
          <Button
            variant="ghost"
            className={styles.entry}
            onClick={() =>
              navigate(
                `/threads/${encodeURIComponent(project.lead_thread_id!)}`,
              )
            }
          >
            <LeadIcon />
            <span>Project Lead</span>
          </Button>
        )}
        <ErrorNotice error={lead.error} retry={() => void lead.refetch()} />
      </>
    );
  return null;
}
