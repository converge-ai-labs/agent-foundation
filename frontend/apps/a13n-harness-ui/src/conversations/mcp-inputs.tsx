import { useTranslation } from "../i18n";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, McpElicitationForm } from "a13n-ui";
import { useTransport } from "../transport/context";
import {
  ApiError,
  isConnectionError,
  result,
  type Schema,
} from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./decisions.module.css";
import conversation from "./conversation.module.css";

type Request = Schema<"McpInputRequestView">;
type Response = Schema<"McpInputResponse">;

export function useMcpInputRequests(threadId: string, enabled = true) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "mcp-inputs"],
    enabled,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/mcp/inputs", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}

export function McpInputs({
  threadId,
  requests,
  reconcile,
}: {
  threadId: string;
  requests?: Request[];
  reconcile: () => void;
}) {
  return (
    <>
      {requests
        ?.filter((request) => request.state === "pending")
        .map((request) => (
          <McpInput
            key={request.request_id}
            threadId={threadId}
            request={request}
            reconcile={reconcile}
          />
        ))}
    </>
  );
}

function McpInput({
  threadId,
  request,
  reconcile,
}: {
  threadId: string;
  request: Request;
  reconcile: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const [submitted, setSubmitted] = useState<Response>();
  const answer = useMutation({
    mutationFn: (response: Response) =>
      result(
        client.POST(
          "/api/threads/{thread_id}/mcp/inputs/{request_id}/response",
          {
            params: {
              path: { thread_id: threadId, request_id: request.request_id },
            },
            body: response,
          },
        ),
      ),
    onSuccess: () => {
      void queries.invalidateQueries({
        queryKey: ["thread", threadId, "mcp-inputs"],
      });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "mcp_input_invalid")
        setSubmitted(undefined);
      reconcile();
    },
  });
  function send(response: Response) {
    setSubmitted(response);
    answer.mutate(response);
  }
  const locked = answer.isPending || submitted !== undefined;
  return (
    <section className={conversation.decision}>
      <McpElicitationForm
        request={request}
        send={send}
        locked={locked}
        t={t}
        context={
          request.thread_id !== threadId && (
            <p className={styles.hint}>
              {t("From child Thread {{id}}", { id: request.thread_id })}
            </p>
          )
        }
        feedback={
          <>
            {answer.isPending && (
              <p role="status" className={styles.hint}>
                {t("Sending response…")}
              </p>
            )}
            {answer.error && <ErrorNotice error={answer.error} />}
            {answer.error &&
              (!(answer.error instanceof ApiError) ||
                isConnectionError(answer.error)) && (
                <>
                  <p className={styles.hint}>
                    {t(
                      "Couldn't confirm delivery. Check the request before retrying. The tool itself won't run again.",
                    )}
                  </p>
                  <div className={styles.actions}>
                    <Button variant="ghost" onClick={reconcile}>
                      {t("Check request")}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={answer.isPending || !submitted}
                      onClick={() => submitted && answer.mutate(submitted)}
                    >
                      {t("Retry response")}
                    </Button>
                  </div>
                </>
              )}
          </>
        }
      />
    </section>
  );
}
