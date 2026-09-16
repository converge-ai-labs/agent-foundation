import {
  Button,
  Checkbox,
  ChoiceField,
  DisclosureSection,
  Label,
} from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView, TextAreaField } from "../../shared/form";
import styles from "../conversations/conversations.module.css";
import { QuestionResponse, readQuestions } from "./questions";

export function ConfigurationFeedback({
  run,
  thread,
  actions,
  accepted,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  actions: Schema["PendingActionResource"][];
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const [answers, setAnswers] = useState<
      Record<
        string,
        {
          action: string;
          value: string;
          structured?: boolean;
          reasonMode?: boolean;
        }
      >
    >({}),
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
  function change(
    id: string,
    answer: {
      action: string;
      value: string;
      structured?: boolean;
      reasonMode?: boolean;
    },
  ) {
    setAnswers((previous) => ({ ...previous, [id]: answer }));
    setKey(crypto.randomUUID());
  }
  return (
    <section className={styles.pending}>
      <h3>{t("Your response is needed")}</h3>
      <p>
        {t(
          "Review every action before continuing. The agent resumes with this complete set of responses.",
        )}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <fieldset
          disabled={mutation.isPending || !can("run.feedback")}
          className={styles.composerFields}
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
                <QuestionResponse
                  key={action.call_id}
                  questions={questions}
                  onChange={(value) => change(action.call_id, value)}
                />
              );
            const details =
              action.kind === "approval"
                ? approvalDetails(action.presentation)
                : null;
            return (
              <div key={action.call_id} className={styles.pendingAction}>
                <strong>{action.tool_name ?? action.call_id}</strong>
                <small>{t(action.kind)}</small>
                {details && (
                  <div className={styles.approvalDetails}>
                    {details.target && (
                      <p>
                        <span>{t("Target")}</span> {details.target}
                      </p>
                    )}
                    {details.risk && (
                      <p>
                        <span>{t("Risk")}</span> {t(details.risk)}
                      </p>
                    )}
                    {details.reason && (
                      <p>
                        <span>{t("Review reason")}</span> {t(details.reason)}
                      </p>
                    )}
                  </div>
                )}
                {action.presentation != null && !details && (
                  <DisclosureSection title={<>{t("Request details")}</>}>
                    <JsonView value={action.presentation} />
                  </DisclosureSection>
                )}
                {action.kind === "approval" ? (
                  <div className={styles.approvalChoices}>
                    {["approve", "reject", "reject_with_reason"].map(
                      (choice) => (
                        <Button
                          key={choice}
                          type="button"
                          variant={
                            answer.action ===
                              (choice === "reject_with_reason"
                                ? "reject"
                                : choice) &&
                            (choice === "reject_with_reason"
                              ? !!answer.reasonMode
                              : !answer.reasonMode)
                              ? "default"
                              : "outline"
                          }
                          aria-pressed={
                            answer.action ===
                              (choice === "reject_with_reason"
                                ? "reject"
                                : choice) &&
                            (choice === "reject_with_reason"
                              ? !!answer.reasonMode
                              : !answer.reasonMode)
                          }
                          onClick={() =>
                            change(action.call_id, {
                              ...answer,
                              action:
                                choice === "reject_with_reason"
                                  ? "reject"
                                  : choice,
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
                      ),
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
                      <Label className="flex items-center gap-2">
                        <Checkbox
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
        </fieldset>
        <ErrorNotice error={mutation.error} />
      </form>
    </section>
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
