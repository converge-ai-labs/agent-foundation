import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./conversation.module.css";

type Response = Schema<"DecisionResponseBatch">["responses"][number];
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
  const [responses, setResponses] = useState<Partial<Record<string, Response>>>(
    {},
  );
  const [unknown, setUnknown] = useState(false);
  const remaining = useDecisionCountdown(batch.expires_at, batch.server_time);
  const expired = remaining === 0;
  useEffect(() => {
    // Expiry belongs to the App. Refetch only; never submit or retry from a timer.
    if (expired) reconcile();
  }, [expired, reconcile]);
  const send = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/threads/{thread_id}/decisions", {
          params: { path: { thread_id: threadId } },
          body: {
            expected_continuation_id: batch.continuation_id,
            responses: batch.requests.map(
              (request) => responses[request.request_id]!,
            ),
          },
        }),
      ),
    onSuccess: reconcile,
    onError: (error) => {
      if (!(error instanceof ApiError) || error.status >= 500) setUnknown(true);
      reconcile();
    },
  });
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
          if (
            !unknown &&
            !expired &&
            !send.isPending &&
            !send.isSuccess &&
            !send.isError &&
            batch.requests.every((request) => responses[request.request_id])
          )
            send.mutate();
        }}
      >
        <fieldset
          className={styles.responseInputs}
          disabled={send.isPending || unknown || send.isSuccess || expired}
        >
          {batch.requests.map((request) => (
            <DecisionInput
              key={request.request_id}
              request={request}
              onChange={(value) => {
                if (send.isPending || unknown || send.isSuccess || expired)
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
            Response accepted. Operation: {send.data.receipt_id}
          </p>
        )}
        {unknown && (
          <p role="status">
            Acknowledgement unavailable. Refresh the pending request; this
            response will not be retried automatically.
          </p>
        )}
        <div>
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
          <Button variant="ghost" onClick={reconcile}>
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

function DecisionInput({
  request,
  onChange,
}: {
  request: Schema<"DecisionRequestView">;
  onChange: (response: Response | undefined) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [choice, setChoice] = useState("");
  const [text, setText] = useState("");
  const [invalid, setInvalid] = useState("");
  if (request.kind === "question") {
    const answer = (question: string, value: string | string[]) => {
      const next = { ...answers, [question]: value };
      setAnswers(next);
      if (
        request.questions.every((item) =>
          typeof next[item.question] === "string"
            ? (next[item.question] as string).trim()
            : (next[item.question] as string[] | undefined)?.length,
        )
      )
        onChange({
          kind: "question",
          request_id: request.request_id,
          answers: next,
        });
      else onChange(undefined);
    };
    return (
      <div className={styles.form}>
        {request.questions.map((question) => (
          <fieldset key={question.question} className={styles.question}>
            <legend>{question.header}</legend>
            <p>{question.question}</p>
            {question.options.map((option) => (
              <label key={option.label} className={styles.answerOption}>
                <input
                  type={question.multi_select ? "checkbox" : "radio"}
                  name={`${request.request_id}:${question.question}`}
                  checked={
                    question.multi_select
                      ? Array.isArray(answers[question.question]) &&
                        answers[question.question].includes(option.label)
                      : answers[question.question] === option.label
                  }
                  onChange={(event) => {
                    if (!question.multi_select)
                      answer(question.question, option.label);
                    else {
                      const selected = Array.isArray(answers[question.question])
                        ? (answers[question.question] as string[])
                        : [];
                      answer(
                        question.question,
                        event.target.checked
                          ? [...selected, option.label]
                          : selected.filter((value) => value !== option.label),
                      );
                    }
                  }}
                />
                <span>
                  <strong>{option.label}</strong>{" "}
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
            <TextField
              label="Or write your own answer"
              value={
                typeof answers[question.question] === "string" &&
                !question.options.some(
                  (option) => option.label === answers[question.question],
                )
                  ? (answers[question.question] as string)
                  : ""
              }
              onChange={(value) => answer(question.question, value)}
            />
          </fieldset>
        ))}
      </div>
    );
  }
  function update(value: string, content: string) {
    setChoice(value);
    setText(content);
    setInvalid("");
    if (!value) {
      onChange(undefined);
      return;
    }
    if (request.kind === "approval") {
      let override_arguments: Record<string, Schema<"JsonValue">> | undefined;
      if (value === "override") {
        try {
          const parsed: unknown = JSON.parse(content);
          if (
            typeof parsed !== "object" ||
            parsed === null ||
            Array.isArray(parsed)
          )
            throw new Error("Expected an object");
          override_arguments = parsed as Record<string, Schema<"JsonValue">>;
        } catch {
          setInvalid("Enter a valid JSON object for replacement arguments.");
          onChange(undefined);
          return;
        }
      }
      onChange({
        kind: "approval",
        request_id: request.request_id,
        approved: value !== "deny",
        ...(override_arguments ? { override_arguments } : {}),
        ...(value === "deny" ? { denial_message: content || null } : {}),
      });
    } else if (value === "deny")
      onChange({
        kind: "external",
        request_id: request.request_id,
        denied: true,
        denial_message: content.trim() || "Denied.",
      });
    else {
      try {
        onChange({
          kind: "external",
          request_id: request.request_id,
          result: JSON.parse(content || "null"),
        });
      } catch {
        setInvalid("Enter a valid JSON result.");
        onChange(undefined);
      }
    }
  }
  return (
    <fieldset className={styles.question}>
      <legend>{request.tool_name}</legend>
      <pre className={styles.code}>
        {request.arguments_omitted
          ? "Arguments omitted by the server. Do not approve content you cannot inspect."
          : JSON.stringify(request.arguments, null, 2)}
      </pre>
      <ChoiceField
        label={request.kind === "approval" ? "Approval" : "External result"}
        value={choice}
        onValueChange={(value) =>
          update(
            value,
            value === "override" && !text
              ? JSON.stringify(request.arguments, null, 2)
              : text,
          )
        }
        options={[
          { value: "", label: "Choose a response" },
          {
            value: request.kind === "approval" ? "approve" : "result",
            label: request.kind === "approval" ? "Approve" : "Provide a result",
          },
          ...(request.kind === "approval" && request.override_allowed !== false
            ? [{ value: "override", label: "Approve with edited arguments" }]
            : []),
          { value: "deny", label: "Deny" },
        ]}
      />
      {(choice === "deny" ||
        choice === "override" ||
        (request.kind === "external" && choice === "result")) && (
        <TextField
          label={
            choice === "deny"
              ? "Reason (optional)"
              : choice === "override"
                ? "Replacement arguments (JSON object)"
                : "Result (JSON)"
          }
          value={text}
          onChange={(value) => update(choice, value)}
        />
      )}
      {invalid && <p role="alert">{invalid}</p>}
    </fieldset>
  );
}
