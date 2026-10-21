import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import styles from "./work-inspector.module.css";

function Execution({
  item,
  refresh,
}: {
  item: Schema<"ToolExecutionView">;
  refresh: () => void;
}) {
  const { client } = useTransport();
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
          <span>{item.tool_name}</span>{" "}
          <Badge
            variant={
              item.status === "running" || item.status === "queued"
                ? "info"
                : item.status === "failed" || item.status === "lost"
                  ? "error"
                  : "secondary"
            }
          >
            {item.status}
          </Badge>
        </summary>
        <dl>
          <div>
            <dt>Execution</dt>
            <dd>{item.execution_id}</dd>
          </div>
          <div>
            <dt>Run</dt>
            <dd>{item.run_id}</dd>
          </div>
          <div>
            <dt>Result</dt>
            <dd>
              {item.delivery_acknowledged
                ? "Received by agent"
                : item.result_available
                  ? "Available to agent"
                  : "Not available"}
            </dd>
          </div>
        </dl>
        <ErrorNotice error={cancel.error} />
        {cancel.isSuccess && (
          <p role="status">
            Cancellation requested. The status shows when execution finishes.
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
            Cancel execution
          </Button>
        )}
      </details>
    </li>
  );
}

export function ToolExecutions({
  threadId,
  rootThreadId = threadId,
}: {
  threadId: string;
  rootThreadId?: string;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [offset, setOffset] = useState(0);
  const key = ["thread", rootThreadId, "tool-executions", threadId];
  const page = useQuery({
    queryKey: [...key, offset],
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
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: key });
  };
  return (
    <section aria-label="Tool executions">
      <p className={styles.caption}>
        Retained tool calls in this app session. Results are retrieved by the
        agent; inspecting status does not consume them.
      </p>
      <ErrorNotice error={page.error} retry={refresh} />
      {page.isPending && <p role="status">Loading tool executions…</p>}
      {page.data?.length === 0 && <p>No tool executions on this page.</p>}
      <ul className={styles.tasks}>
        {page.data?.map((item) => (
          <Execution key={item.execution_id} item={item} refresh={refresh} />
        ))}
      </ul>
      <Button
        variant="ghost"
        size="sm"
        disabled={offset === 0 || page.isFetching}
        onClick={() => setOffset(offset - 20)}
      >
        Previous
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={page.data?.length !== 20 || page.isFetching}
        onClick={() => setOffset(offset + 20)}
      >
        Next
      </Button>
      <Button
        variant="ghost"
        size="sm"
        loading={page.isFetching}
        onClick={refresh}
      >
        Refresh
      </Button>
    </section>
  );
}
