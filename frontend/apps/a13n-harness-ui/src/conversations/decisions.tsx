import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";
import { useResults } from "./results";

import {
  DecisionInput,
  type DecisionResponse as Response,
} from "./decision-inputs";
export function Decisions({
  threadId,
  continuation,
  reconcile,
}: {
  threadId: string;
  continuation?: string | null;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const batch = useQuery({
    queryKey: ["thread", threadId, "decisions", continuation],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/decisions", {
          params: {
            path: { thread_id: threadId },
            query: { expected_continuation_id: continuation ?? undefined },
          },
          signal,
        }),
      ),
  });
  return (
    <>
      <ErrorNotice error={batch.error} retry={() => void batch.refetch()} />
      {batch.data && (
        <DecisionForm
          key={batch.data.continuation_id}
          threadId={threadId}
          batch={batch.data}
          reconcile={reconcile}
        />
      )}
    </>
  );
}
export function DecisionForm({
  threadId,
  batch,
  reconcile,
}: {
  threadId: string;
  batch: Schema<"DecisionBatchView">;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const { tracker: results } = useResults();
  const [responses, setResponses] = useState<Partial<Record<string, Response>>>(
    {},
  );
  const [unknown, setUnknown] = useState(false);
  const submitting = useRef(false);
  const singleApproval =
    batch.requests.length === 1 && batch.requests[0].kind === "approval";
  const remaining = useDecisionCountdown(batch.expires_at, batch.server_time);
  const expired = remaining === 0;
  useEffect(() => {
    // Expiry belongs to the App. Refetch only; never submit or retry from a timer.
    if (expired) reconcile();
  }, [expired, reconcile]);
  const send = useMutation({
    mutationFn: async (submitted: Response[]) => {
      await results?.beforeRun(threadId);
      return result(
        client.POST("/api/threads/{thread_id}/decisions", {
          params: { path: { thread_id: threadId } },
          body: {
            expected_continuation_id: batch.continuation_id,
            responses: submitted,
          },
        }),
      );
    },
    retry: false,
    onSuccess: reconcile,
    onError: (error) => {
      if (!(error instanceof ApiError) || error.status >= 500) setUnknown(true);
      submitting.current = false;
      reconcile();
    },
  });
  const stale = send.error instanceof ApiError && send.error.status === 409;
  function submit(values: Partial<Record<string, Response>>) {
    if (
      submitting.current ||
      unknown ||
      expired ||
      send.isPending ||
      send.isSuccess ||
      send.isError
    )
      return;
    const complete = batch.requests.map(
      (request) => values[request.request_id],
    );
    if (complete.some((response) => !response)) return;
    submitting.current = true;
    send.mutate(complete as Response[]);
  }
  return (
    <section className={styles.decision} aria-label="Pending decisions">
      <h2>Your response is needed</h2>
      <p>
        Respond to this request set together. Another participant may resolve it
        first.
      </p>
      {remaining !== null && (
        <p role="status" aria-live="off">
          {expired
            ? "Response window ended. Waiting for the server to confirm the outcome."
            : `Submit within ${remaining}s. On timeout, the server continues without answers or approvals, even if you leave this page. Unsubmitted answers are discarded.`}
        </p>
      )}
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (!singleApproval) submit(responses);
        }}
      >
        <fieldset
          className={styles.responseInputs}
          disabled={
            send.isPending || unknown || stale || send.isSuccess || expired
          }
        >
          {batch.requests.map((request) => (
            <DecisionInput
              key={request.request_id}
              request={request}
              onSubmit={
                singleApproval
                  ? (response) => submit({ [request.request_id]: response })
                  : undefined
              }
              onChange={(value) => {
                if (
                  send.isPending ||
                  unknown ||
                  stale ||
                  send.isSuccess ||
                  expired
                )
                  return;
                if (!unknown && !send.isSuccess) send.reset();
                setResponses((previous) => ({
                  ...previous,
                  [request.request_id]: value,
                }));
              }}
            />
          ))}
        </fieldset>
        <ErrorNotice error={send.error} />
        {send.isSuccess && (
          <p role="status">
            Response accepted; execution has not been confirmed. Operation:{" "}
            {send.data.receipt_id}
          </p>
        )}
        {unknown && (
          <p role="status">
            Acknowledgement unavailable. Refresh the pending request; this
            response will not be retried automatically.
          </p>
        )}
        <div>
          {!singleApproval && (
            <Button
              type="submit"
              disabled={
                unknown ||
                expired ||
                send.isSuccess ||
                send.isError ||
                batch.requests.some((request) => !responses[request.request_id])
              }
              loading={send.isPending}
            >
              Submit responses
            </Button>
          )}
          <Button type="button" variant="ghost" onClick={reconcile}>
            Refresh request
          </Button>
        </div>
      </form>
    </section>
  );
}
function useDecisionCountdown(
  expiresAt?: string | null,
  serverTime?: string | null,
) {
  const [remaining, setRemaining] = useState<number | null>(null);
  useEffect(() => {
    if (!expiresAt) {
      setRemaining(null);
      return;
    }
    // Anchor to server time so a participant's wall-clock skew does not change
    // the advertised window. Timers only repaint; the server enforces admission.
    const duration =
      Date.parse(expiresAt) -
      (serverTime ? Date.parse(serverTime) : Date.now());
    const started = performance.now();
    const update = () =>
      setRemaining(
        Math.max(
          0,
          Math.ceil((duration - (performance.now() - started)) / 1000),
        ),
      );
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [expiresAt, serverTime]);
  return remaining;
}
