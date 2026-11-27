import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "../i18n";
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
import { CopyCommand, record } from "./decision-evidence";
import { QuestionInput } from "./question-input";

type Request = Schema<"McpInputRequestView">;
type Response = Schema<"McpInputResponse">;
type PermissionDetail = Schema<"PermissionRequestDetail">;
type PermissionOption = Schema<"PermissionOptionView">;

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

function permissionHeading(
  toolKind: string | null | undefined,
  t: (key: string) => string,
): string {
  switch (toolKind) {
    case "execute":
      return t("Command permission");
    case "edit":
      return t("File edit permission");
    case "delete":
      return t("Delete permission");
    case "move":
      return t("Move permission");
    case "read":
      return t("File read permission");
    case "search":
      return t("Search permission");
    case "fetch":
      return t("Network permission");
    case "switch_mode":
      return t("Mode change permission");
    default:
      return t("Permission requested");
  }
}

function PermissionCard({
  request,
  detail,
  locked,
  pendingOptionId,
  send,
  context,
  feedback,
}: {
  request: Request;
  detail: PermissionDetail;
  locked: boolean;
  pendingOptionId?: string;
  send: (response: Response) => void;
  context: ReactNode;
  feedback: ReactNode;
}) {
  const { t } = useTranslation();
  const options = detail.options ?? [];
  const allowOptions = options.filter(
    (option) => option.kind === "allow_once" || option.kind === "allow_always",
  );
  const rejectOptions = options.filter(
    (option) =>
      option.kind === "reject_once" || option.kind === "reject_always",
  );
  const orderedOptions = [...allowOptions, ...rejectOptions];
  const primaryOptionId =
    allowOptions.find((option) => option.kind === "allow_once")?.option_id ??
    allowOptions[0]?.option_id;
  const optionVariant = (
    option: PermissionOption,
  ): "default" | "outline" | "ghost" => {
    if (option.option_id === primaryOptionId) return "default";
    if (option.kind === "allow_once" || option.kind === "allow_always")
      return "outline";
    if (option.kind === "reject_once" && allowOptions.length <= 1)
      return "outline";
    return "ghost";
  };
  const hasContext = Boolean(
    detail.cwd || detail.locations?.length || detail.environment_keys?.length,
  );
  const extraArguments = record(detail.arguments);
  return (
    <section
      className={conversation.decision}
      aria-label={t("Permission requested")}
    >
      <div className={styles.request}>
        <div className={styles.heading}>
          <h3>{permissionHeading(detail.tool_kind, t)}</h3>
          <span className={styles.tool}>
            {request.server_name ?? request.server_id}
          </span>
        </div>
        {context}
        {detail.tool_name && !detail.command && (
          <span className={styles.tool}>{detail.tool_name}</span>
        )}
        {request.message && <p className={styles.reason}>{request.message}</p>}
        {detail.command && (
          <div className={styles.commandBlock}>
            <div className={styles.heading}>
              <span className={styles.commandLabel}>{t("Command")}</span>
              <CopyCommand command={detail.command} />
            </div>
            <pre className={styles.code}>
              <code>{detail.command}</code>
            </pre>
          </div>
        )}
        {hasContext && (
          <dl className={styles.context}>
            {detail.cwd && (
              <>
                <dt>{t("Working directory")}</dt>
                <dd>{detail.cwd}</dd>
              </>
            )}
            {!!detail.locations?.length && (
              <>
                <dt>{t("Locations")}</dt>
                <dd className={styles.locations}>
                  {detail.locations.map((location) => (
                    <span key={location}>{location}</span>
                  ))}
                </dd>
              </>
            )}
            {!!detail.environment_keys?.length && (
              <>
                <dt>{t("Environment variables")}</dt>
                <dd>
                  {t("{{variables}} (values hidden)", {
                    variables: detail.environment_keys.join(", "),
                  })}
                </dd>
              </>
            )}
          </dl>
        )}
        {extraArguments && Object.keys(extraArguments).length > 0 && (
          <details className={styles.evidence}>
            <summary>{t("Details")}</summary>
            <pre className={styles.code}>
              {JSON.stringify(extraArguments, null, 2)}
            </pre>
          </details>
        )}
        <div className={styles.actions}>
          {orderedOptions.map((option) => (
            <Button
              key={option.option_id}
              type="button"
              variant={optionVariant(option)}
              disabled={locked}
              loading={pendingOptionId === option.option_id}
              onClick={() =>
                send({
                  action: "accept",
                  content: { option: option.option_id },
                })
              }
            >
              {option.name}
            </Button>
          ))}
          {rejectOptions.length === 0 && (
            <Button
              type="button"
              variant="outline"
              disabled={locked}
              onClick={() => send({ action: "decline" })}
            >
              {t("Deny")}
            </Button>
          )}
        </div>
        {feedback}
      </div>
    </section>
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
  const privateFormId = useId();
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
  if (request.mode === "permission" && request.permission) {
    const pendingOption =
      answer.isPending && typeof submitted?.content?.option === "string"
        ? submitted.content.option
        : undefined;
    return (
      <PermissionCard
        request={request}
        detail={request.permission}
        locked={locked}
        pendingOptionId={pendingOption}
        send={send}
        context={context}
        feedback={feedback}
      />
    );
  }
  if (
    !request.private_form &&
    request.mode !== "question" &&
    request.mode !== "permission"
  ) {
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
  const isAcp = request.source === "acp";
  const heading = request.private_form
    ? t("Input requested")
    : request.mode === "question" && isAcp
      ? (request.questions?.length ?? 0) > 1
        ? t("Questions")
        : t("Question")
      : t("Input requested");
  return (
    <section
      className={conversation.decision}
      aria-label={isAcp ? t("Agent input request") : t("MCP input request")}
    >
      <div className={styles.request}>
        <div className={styles.heading}>
          <h3>{heading}</h3>
          <span className={styles.tool}>
            {request.server_name ?? request.server_id}
          </span>
        </div>
        {context}
        {request.message && <p className={styles.reason}>{request.message}</p>}
        {request.private_form ? (
          <>
            <a
              className={styles.link}
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
              id={privateFormId}
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
              </div>
            </form>
          </>
        ) : (
          <>
            {!isAcp && (
              <p className={styles.hint}>
                {t(
                  "Your response goes to this MCP server. Don't include passwords, tokens or other secrets.",
                )}
              </p>
            )}
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
          {request.private_form && (
            <Button type="submit" form={privateFormId} disabled={locked}>
              {t("Send response")}
            </Button>
          )}
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
