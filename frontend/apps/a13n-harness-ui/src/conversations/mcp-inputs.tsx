import { useTranslation } from "../i18n";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, McpElicitationForm } from "a13n-ui";
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
import { record } from "./decision-evidence";
import { QuestionInput } from "./question-input";

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

export function McpInput({
  threadId,
  request,
  reconcile,
  submitResponse,
}: {
  threadId: string;
  request: Request;
  reconcile: () => void;
  submitResponse?: (response: Response) => Promise<Request>;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const schema = record(request.schema);
  const properties = record(schema?.properties) ?? {};
  const required = Array.isArray(schema?.required) ? schema.required : [];
  const [privateValues, setPrivateValues] = useState<
    NonNullable<Response["content"]>
  >({});
  const [submitted, setSubmitted] = useState<Response>();
  const [questionAnswer, setQuestionAnswer] = useState<Response>();
  const answer = useMutation({
    mutationFn: (response: Response) =>
      submitResponse
        ? submitResponse(response)
        : result(
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
      if (submitResponse) {
        reconcile();
        return;
      }
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
    if (!request.private_form) setSubmitted(response);
    answer.mutate(response);
  }
  const locked =
    answer.isPending || answer.isSuccess || submitted !== undefined;
  const context = request.thread_id !== threadId && (
    <p className={styles.hint}>
      {t("From child Thread {{id}}", { id: request.thread_id })}
    </p>
  );
  const feedback = (
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
  );
  if (!request.private_form && request.mode !== "question") {
    return (
      <section className={conversation.decision}>
        <McpElicitationForm
          request={{ ...request, mode: request.mode }}
          send={send}
          locked={locked}
          t={t}
          context={context}
          feedback={feedback}
        />
      </section>
    );
  }
  return (
    <section
      className={conversation.decision}
      aria-label={t("MCP input request")}
    >
      <div className={styles.request}>
        <div className={styles.heading}>
          <h3>{t("Input requested")}</h3>
          <span className={styles.tool}>
            {request.server_name ?? request.server_id}
          </span>
        </div>
        {context}
        {request.message && <p className={styles.reason}>{request.message}</p>}
        {request.private_form ? (
          <>
            <a
              href={request.url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t("Open Google sign-in")}
            </a>
            <p className={styles.hint}>
              {t(
                "After signing in, paste the callback URL or authorization code shown in your browser.",
              )}
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!locked) send({ action: "accept", content: privateValues });
              }}
            >
              <div className={styles.request}>
                {Object.keys(properties).map((name) => {
                  const label = t("Callback URL or authorization code");
                  return (
                    <FormField key={name} label={label}>
                      <Input
                        aria-label={label}
                        disabled={locked}
                        required={required.includes(name)}
                        type="password"
                        value={
                          privateValues[name] === undefined
                            ? ""
                            : String(privateValues[name])
                        }
                        onChange={(event) =>
                          setPrivateValues((old) => {
                            const next = { ...old };
                            if (
                              event.target.value === "" &&
                              !required.includes(name)
                            )
                              delete next[name];
                            else next[name] = event.target.value;
                            return next;
                          })
                        }
                      />
                    </FormField>
                  );
                })}
                <div className={styles.actions}>
                  <Button type="submit" disabled={locked}>
                    {t("Send response")}
                  </Button>
                </div>
              </div>
            </form>
          </>
        ) : (
          <>
            <p className={styles.hint}>
              {t(
                "Your response goes to this MCP server. Don't include passwords, tokens or other secrets.",
              )}
            </p>
            <fieldset disabled={locked}>
              <QuestionInput
                request={{
                  request_id: request.request_id,
                  questions: (request.questions ?? []).map(
                    ({ multiSelect, ...question }) => ({
                      ...question,
                      multi_select: multiSelect,
                    }),
                  ),
                }}
                onChange={(response) =>
                  setQuestionAnswer(
                    response && {
                      action: "accept",
                      content: {
                        answers: response.answers,
                        response: response.response,
                      },
                    },
                  )
                }
              />
            </fieldset>
          </>
        )}
        <div className={styles.actions}>
          {request.mode === "question" && (
            <Button
              disabled={locked || !questionAnswer}
              onClick={() => questionAnswer && send(questionAnswer)}
            >
              {t("Send response")}
            </Button>
          )}
          <Button
            variant="ghost"
            disabled={locked}
            onClick={() => send({ action: "decline" })}
          >
            {t("Decline")}
          </Button>
          <Button
            variant="ghost"
            disabled={locked}
            onClick={() => send({ action: "cancel" })}
          >
            {t("Cancel request")}
          </Button>
        </div>
        {feedback}
      </div>
    </section>
  );
}
