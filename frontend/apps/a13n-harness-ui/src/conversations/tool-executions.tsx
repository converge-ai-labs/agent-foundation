import { useTranslation } from "../i18n";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowClockwise } from "@phosphor-icons/react";
import { HelpTip } from "../shell/help-tip";
import { Badge, Button } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { DebugOnly } from "../shell/debug";
import styles from "./work-inspector.module.css";

function Execution({
  item,
  refresh,
}: {
  item: Schema<"ToolExecutionView">;
  refresh: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const statusLabels = {
    queued: t("Queued"),
    running: t("Running"),
    succeeded: t("Succeeded"),
    failed: t("Failed"),
    cancelled: t("Cancelled"),
    lost: t("Lost"),
  } satisfies Record<Schema<"ToolExecutionView">["status"], string>;
  const cancel = useMutation({
    mutationFn: () =>
      result(
        client.POST(
          "/api/threads/{thread_id}/tool-executions/{execution_id}/cancel",
          {
            params: {
              path: {
                thread_id: item.thread_id,
                execution_id: item.execution_id,
              },
            },
          },
        ),
      ),
    onSettled: refresh,
  });
  return (
    <li>
      <details>
        <summary className={styles.processSummary}>
          <span>{item.tool_name}</span>
          <Badge
            variant={
              item.status === "running" || item.status === "queued"
                ? "info"
                : item.status === "failed" || item.status === "lost"
                  ? "error"
                  : "secondary"
            }
          >
            {statusLabels[item.status]}
          </Badge>
        </summary>
        <div className={styles.executionBody}>
          <dl>
            <DebugOnly>
              <div>
                <dt>{t("Execution")}</dt>
                <dd>{item.execution_id}</dd>
              </div>
              <div>
                <dt>{t("Run")}</dt>
                <dd>{item.run_id}</dd>
              </div>
            </DebugOnly>
            <div>
              <dt>{t("Result")}</dt>
              <dd>
                {item.delivery_acknowledged
                  ? t("Received by agent")
                  : item.result_available
                    ? t("Available to agent")
                    : t("Not available")}
              </dd>
            </div>
          </dl>
          <ErrorNotice error={cancel.error} />
          {cancel.isSuccess && (
            <p role="status">
              {t(
                "Cancellation requested. The status will update when the tool stops.",
              )}
            </p>
          )}
          {item.available_actions?.includes("cancel") && (
            <Button
              variant="outline"
              size="sm"
              loading={cancel.isPending}
              disabled={cancel.isSuccess}
              onClick={() => cancel.mutate()}
            >
              {t("Cancel execution")}
            </Button>
          )}
        </div>
      </details>
    </li>
  );
}

export function useToolExecutionsPage(
  threadId: string,
  rootThreadId: string,
  offset: number,
  enabled = true,
) {
  const { client } = useTransport();
  return useQuery({
    enabled,
    queryKey: ["thread", rootThreadId, "tool-executions", threadId, offset],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/tool-executions", {
          params: {
            path: { thread_id: threadId },
            query: { offset, limit: 20 },
          },
          signal,
        }),
      ),
  });
}

export function ToolExecutions({
  threadId,
  rootThreadId = threadId,
}: {
  threadId: string;
  rootThreadId?: string;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [offset, setOffset] = useState(0);
  const key = ["thread", rootThreadId, "tool-executions", threadId];
  const page = useToolExecutionsPage(threadId, rootThreadId, offset);
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: key });
  };
  return (
    <section aria-label={t("Tool calls")}>
      <div className={styles.panelToolbar}>
        <span>{t("Tool calls")}</span>
        <HelpTip label={t("About tool calls")}>
          {t(
            "Tools used in this app session. Viewing a result keeps it available to the agent.",
          )}
        </HelpTip>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Refresh tool calls")}
          loading={page.isFetching}
          onClick={refresh}
        >
          <ArrowClockwise />
        </Button>
      </div>
      <ErrorNotice error={page.error} retry={refresh} />
      {page.isPending && <p role="status">{t("Loading tool executions…")}</p>}
      {page.data?.length === 0 && (
        <p className={styles.emptyState}>
          {offset > 0 ? t("No more tool calls.") : t("No tool calls yet.")}
        </p>
      )}
      <ul className={styles.tasks}>
        {page.data?.map((item) => (
          <Execution key={item.execution_id} item={item} refresh={refresh} />
        ))}
      </ul>
      {(offset > 0 || page.data?.length === 20) && (
        <div className={styles.actions}>
          <Button
            variant="ghost"
            size="sm"
            disabled={offset === 0 || page.isFetching}
            onClick={() => setOffset(offset - 20)}
          >
            {t("Previous")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={page.data?.length !== 20 || page.isFetching}
            onClick={() => setOffset(offset + 20)}
          >
            {t("Next")}
          </Button>
        </div>
      )}
    </section>
  );
}
