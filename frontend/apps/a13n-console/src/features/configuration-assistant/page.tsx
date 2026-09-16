import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import { Composer } from "../conversations/composer";
import { isActiveRun, invalidateConversation } from "../conversations/api";
import { RunInspector } from "../conversations/inspector";
import { RunContent } from "../conversations/run";
import { useRun } from "../conversations/queries";
import { useAssistantReadiness, useConfigurationThread } from "./api";
import { ReadinessNotice } from "./start";
import { DraftReview } from "./review";
import styles from "./configuration.module.css";

export function ConfigurationPage() {
  const { threadId = "" } = useParams();
  return <ConfigurationConversation key={threadId} threadId={threadId} />;
}

function ConfigurationConversation({ threadId }: { threadId: string }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const query = useConfigurationThread(threadId),
    [params] = useSearchParams(),
    navigate = useNavigate();
  const cache = useQueryClient(),
    forkKey = useIdempotency();
  const thread = query.data?.thread,
    draft = query.data?.draft;
  const runId =
    params.get("run") ?? thread?.current_run_id ?? thread?.head_run_id;
  const run = useRun(runId),
    currentRun = useRun(thread?.current_run_id);
  const readiness = useAssistantReadiness(draft?.target_agent_id);
  const branches = useQuery({
    queryKey: ["configuration-branches", workspace.id, thread?.session_id],
    enabled: !!thread,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/configuration-sessions/{session_id}/threads", {
          params: {
            path: { session_id: thread!.session_id },
            query: { limit: 100 },
          },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  async function accepted(receipt: Schema["RunAcceptanceReceipt"]) {
    await invalidateConversation(cache, workspace.id, {
      sessionId: receipt.session_id,
      threadId: receipt.thread_id,
      runId: receipt.run_id,
    });
    await query.refetch();
    navigate(
      `${basePath}/configuration-threads/${receipt.thread_id}?run=${receipt.run_id}`,
    );
  }
  const fork = useMutation({
    mutationFn: () => {
      const body = { fork_from_run_id: runId! };
      return client.http
        .POST("/api/v1/configuration-sessions/{session_id}/threads", {
          params: {
            path: { session_id: thread!.session_id },
            header: commandHeaders(workspace.id, forkKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      forkKey.reset();
      navigate(`${basePath}/configuration-threads/${result.thread.id}`);
    },
  });
  if (query.isPending) return <Loading page />;
  if (!thread || !draft)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const busy =
    !!thread.current_run_id &&
    (!currentRun.data ||
      isActiveRun(currentRun.data.status) ||
      currentRun.data.status === "waiting");
  const composer = (
    <>
      {readiness.data && <ReadinessNotice readiness={readiness.data} />}
      <ErrorNotice
        error={readiness.error}
        retry={() => void readiness.refetch()}
      />
      {draft.status !== "open" && (
        <p className={styles.notice}>
          {t(
            "This draft is closed. Start a new conversation to configure another draft.",
          )}
        </p>
      )}
      <Composer
        disabled={
          busy ||
          draft.status !== "open" ||
          !readiness.data?.ready ||
          !can("run.continue")
        }
        label={t("Send to configuration assistant")}
        submit={async (input, key) =>
          accepted(
            data(
              await client.http.POST(
                "/api/v1/configuration-threads/{thread_id}/inputs",
                {
                  params: {
                    path: { thread_id: thread.id },
                    header: commandHeaders(workspace.id, key),
                  },
                  body: { input, expected_thread_version: thread.version },
                },
              ),
            ),
          )
        }
      />
    </>
  );
  return (
    <div className={styles.workspace}>
      <header className={styles.header}>
        <h1>{t("Configuration assistant")}</h1>
        <nav aria-label={t("Configuration conversations")}>
          <Link to={`${basePath}/configuration/new`}>
            {t("Your conversations")}
          </Link>
          {branches.data?.items.map((branch, index) => (
            <Link
              key={branch.thread.id}
              aria-current={branch.thread.id === threadId ? "page" : undefined}
              to={`${basePath}/configuration-threads/${branch.thread.id}`}
            >
              {t("Thread")} {index + 1}
            </Link>
          ))}
        </nav>
        {runId && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInspectorOpen(true)}
          >
            {t("Run details")}
          </Button>
        )}
        {run.data?.status === "completed" && (
          <Button
            variant="outline"
            size="sm"
            loading={fork.isPending}
            onClick={() => fork.mutate()}
          >
            {t("Fork conversation")}
          </Button>
        )}
      </header>
      <p className={styles.notice}>
        {t(
          "All threads in this conversation edit the same draft. Start a new conversation for an independent candidate.",
        )}
      </p>
      <ErrorNotice error={query.error ?? fork.error ?? branches.error} />
      <ModalFrame
        open={inspectorOpen}
        onOpenChange={setInspectorOpen}
        title={t("Run details")}
        closeLabel={t("Close")}
        size="lg"
      >
        {inspectorOpen && runId && (
          <RunInspector runId={runId} onClose={() => setInspectorOpen(false)} />
        )}
      </ModalFrame>
      <div className={styles.panes}>
        <div
          className={`${styles.conversation} a13n-scrollbar`}
          data-session-stage
        >
          {runId ? (
            <RunContent
              key={runId}
              runId={runId}
              threadId={thread.id}
              sessionId={thread.session_id}
              configuration={{
                composer,
                accepted: (receipt) => void accepted(receipt),
              }}
            />
          ) : (
            <div className={styles.empty}>
              <h2>{t("What should this agent do?")}</h2>
              <p>
                {t(
                  "Describe its purpose, expected results and any tools it needs.",
                )}
              </p>
              {composer}
            </div>
          )}
        </div>
        <DraftReview key={draft.id} draftId={draft.id} />
      </div>
    </div>
  );
}
