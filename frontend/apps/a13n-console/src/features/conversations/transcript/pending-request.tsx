import { Button, ChoiceField, DisclosureSection, Label, Switch } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { JsonView, TextAreaField } from "../../../shared/forms";
import { QuestionResponse, readQuestions } from "./questions";
import styles from "./transcript.module.css";

type Answer = {
  action: string;
  value: string;
  structured?: boolean;
  reasonMode?: boolean;
};

/** The run paused and this console cannot answer for the owning application. */
export function PendingRequests({
  actions,
}: {
  actions: Schema["PendingActionResource"][];
}) {
  const { t } = useTranslation();
  return (
    <PendingGroup
      title={t("Waiting for the application")}
      description={t(
        "The agent paused this run for input or approval. It resumes when the host application responds.",
      )}
    >
      {actions.map((action) => (
        <div key={action.call_id} className={styles.pendingAction}>
          <ActionIdentity action={action} />
          {action.presentation != null && (
            <DisclosureSection
              className={styles.inlineDisclosure}
              title={<>{t("Request details")}</>}
            >
              <JsonView value={action.presentation} />
            </DisclosureSection>
          )}
        </div>
      ))}
    </PendingGroup>
  );
}

/** Every pending action is answered together; the agent resumes with the set. */
export function RunFeedback({
  run,
  thread,
  actions,
  accepted,
  continuation,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  actions: Schema["PendingActionResource"][];
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
  /** The "continue without feedback" escape, shown beside Submit. */
  continuation?: ReactNode;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const [answers, setAnswers] = useState<Record<string, Answer>>({}),
    [key, setKey] = useState(crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: async () => {
      const resolutions: NonNullable<
        Schema["WaitingRunFeedbackRequest"]["resolutions"]
      > = [];
      for (const pending of actions) {
        const answer = answers[pending.call_id];
        if (!answer?.action)
          throw new Error(t("Choose a response for every pending action."));
        if (answer.action === "omit") continue;
        if (answer.action === "approve" || answer.action === "reject") {
          resolutions.push(
            answer.action === "reject"
              ? {
                  action: "reject",
                  call_id: pending.call_id,
                  ...(answer.value.trim()
                    ? { reason: answer.value.trim() }
                    : {}),
                }
              : { action: "approve", call_id: pending.call_id },
          );
          continue;
        }
        let value: Schema["JsonValue"];
        try {
          value =
            answer.action === "respond" && !answer.structured
              ? pending.tool_name === "ask_user_question"
                ? { response: answer.value }
                : answer.value
              : JSON.parse(answer.value);
        } catch {
          throw new Error(t("Tool results and responses must be valid JSON."));
        }
        if (answer.action === "complete")
          resolutions.push({
            action: "complete",
            call_id: pending.call_id,
            result: value,
          });
        else
          resolutions.push({
            action: "respond",
            call_id: pending.call_id,
            response: value,
          });
      }
      return client.http
        .POST("/api/v1/runs/{run_id}/feedback", {
          params: {
            path: { run_id: run.id },
            header: commandHeaders(workspace.id, key),
          },
          body: {
            expected_thread_version: thread.version,
            sealed_state_digest_sha256: run.sealed_state_digest_sha256!,
            resolutions,
          },
        })
        .then(data);
    },
    onSuccess: (receipt) => accepted(receipt),
  });
  function change(id: string, answer: Answer) {
    setAnswers((previous) => ({ ...previous, [id]: answer }));
    setKey(crypto.randomUUID());
  }
  return (
    <PendingGroup
      title={t("Waiting for your response")}
      description={t(
        "Review every action before continuing. The agent resumes with this complete set of responses.",
      )}
    >
      <form
        className={styles.pendingForm}
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <fieldset
          disabled={mutation.isPending || !can("run.feedback")}
          className={styles.pendingFields}
        >
          {actions.map((action) => {
            const answer = answers[action.call_id] ?? { action: "", value: "" };
            const questions =
              action.kind === "user_input" &&
              action.tool_name === "ask_user_question"
                ? readQuestions(action.presentation)
                : null;
            if (questions)
              return (
                <div key={action.call_id} className={styles.pendingAction}>
                  <QuestionResponse
                    questions={questions}
                    onChange={(value) => change(action.call_id, value)}
                  />
                </div>
              );
            const details =
              action.kind === "approval"
                ? approvalDetails(action.presentation)
                : null;
            return (
              <div key={action.call_id} className={styles.pendingAction}>
                <ActionIdentity action={action} />
                {details && (
                  <dl className={styles.approvalDetails}>
                    {details.target && (
                      <>
                        <dt>{t("Target")}</dt>
                        <dd>{details.target}</dd>
                      </>
                    )}
                    {details.risk && (
                      <>
                        <dt>{t("Risk")}</dt>
                        <dd>{t(details.risk)}</dd>
                      </>
                    )}
                    {details.reason && (
                      <>
                        <dt>{t("Review reason")}</dt>
                        <dd>{t(details.reason)}</dd>
                      </>
                    )}
                  </dl>
                )}
                {action.presentation != null && !details && (
                  <DisclosureSection
                    className={styles.inlineDisclosure}
                    title={<>{t("Request details")}</>}
                  >
                    <JsonView value={action.presentation} />
                  </DisclosureSection>
                )}
                {action.kind === "approval" ? (
                  <div className={styles.approvalChoices}>
                    {["approve", "reject", "reject_with_reason"].map(
                      (choice) => {
                        const target =
                          choice === "reject_with_reason" ? "reject" : choice;
                        const pressed =
                          answer.action === target &&
                          (choice === "reject_with_reason"
                            ? !!answer.reasonMode
                            : !answer.reasonMode);
                        return (
                          <Button
                            key={choice}
                            type="button"
                            size="sm"
                            variant={pressed ? "secondary" : "outline"}
                            aria-pressed={pressed}
                            onClick={() =>
                              change(action.call_id, {
                                ...answer,
                                action: target,
                                reasonMode: choice === "reject_with_reason",
                                value:
                                  choice === "reject_with_reason"
                                    ? answer.value
                                    : "",
                              })
                            }
                          >
                            {t(
                              choice === "approve"
                                ? "Approve once"
                                : choice === "reject"
                                  ? "Deny"
                                  : "Deny with reason",
                            )}
                          </Button>
                        );
                      },
                    )}
                  </div>
                ) : (
                  <ChoiceField
                    placeholder={t("Choose a response")}
                    value={answer.action}
                    onValueChange={(value) =>
                      change(action.call_id, { ...answer, action: value })
                    }
                    label={t("Response")}
                    hideLabel
                    options={[
                      {
                        value:
                          action.kind === "client_tool"
                            ? "complete"
                            : "respond",
                        label: t(
                          action.kind === "client_tool"
                            ? "Return tool result"
                            : "Respond",
                        ),
                      },
                      {
                        value: "omit",
                        label: t("Continue without a response"),
                      },
                    ]}
                  />
                )}
                {action.kind === "approval" &&
                  answer.action === "reject" &&
                  answer.reasonMode && (
                    <TextAreaField
                      label={t("Denial reason (optional)")}
                      value={answer.value}
                      onChange={(value) =>
                        change(action.call_id, {
                          ...answer,
                          value: value.slice(0, 2000),
                        })
                      }
                      rows={3}
                    />
                  )}
                {["complete", "respond"].includes(answer.action) && (
                  <>
                    {answer.action === "respond" && (
                      <Label className={styles.pendingSwitch}>
                        <Switch
                          checked={!!answer.structured}
                          onCheckedChange={(checked) =>
                            change(action.call_id, {
                              ...answer,
                              structured: checked === true,
                            })
                          }
                        />
                        {t("Structured response")}
                      </Label>
                    )}
                    <TextAreaField
                      label={t(
                        answer.action === "complete" || answer.structured
                          ? "Response (JSON)"
                          : "Your answer",
                      )}
                      code={answer.action === "complete" || answer.structured}
                      value={answer.value}
                      onChange={(value) =>
                        change(action.call_id, { ...answer, value })
                      }
                    />
                  </>
                )}
              </div>
            );
          })}
          <div className={styles.pendingFooter}>
            {continuation}
            <Button
              type="submit"
              variant="default"
              disabled={
                !actions.every((action) => !!answers[action.call_id]?.action)
              }
              loading={mutation.isPending}
            >
              {t("Submit responses")}
            </Button>
          </div>
        </fieldset>
        <ErrorNotice error={mutation.error} />
      </form>
    </PendingGroup>
  );
}

function PendingGroup({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.pending} aria-label={title}>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </section>
  );
}

function ActionIdentity({
  action,
}: {
  action: Schema["PendingActionResource"];
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.pendingIdentity}>
      <strong>{action.tool_name ?? action.call_id}</strong>
      <small>{t(action.kind)}</small>
    </div>
  );
}

function approvalDetails(
  value: Schema["JsonValue"] | null,
): { target?: string; reason?: string; risk?: string } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const details = value as Record<string, unknown>;
  const { target, reason, risk } = details;
  if (
    Object.keys(details).length === 0 ||
    !Object.keys(details).every((key) =>
      ["target", "reason", "risk"].includes(key),
    ) ||
    (target !== undefined && typeof target !== "string") ||
    (reason !== undefined &&
      (typeof reason !== "string" ||
        ![
          "Tool permission configuration requires approval.",
          "Tool policy requires approval.",
          "Tool reviewer requires approval.",
          "Tool review could not complete.",
        ].includes(reason))) ||
    (risk !== undefined &&
      (typeof risk !== "string" ||
        !["low", "medium", "high", "extra_high"].includes(risk)))
  )
    return null;
  return {
    target: target as string | undefined,
    reason: reason as string | undefined,
    risk: risk as string | undefined,
  };
}
