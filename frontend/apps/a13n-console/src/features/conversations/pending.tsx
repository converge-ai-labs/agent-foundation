import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Button, Select, Checkbox } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView, TextArea } from "../../shared/form";
import { invalidateConversation, runPath } from "./api";
import styles from "./conversations.module.css";

export function PendingFeedback({
  run,
  thread,
  actions,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  actions: Schema["PendingActionResource"][];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient();
  const [answers, setAnswers] = useState<
      Record<string, { action: string; value: string; structured?: boolean }>
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
          resolutions.push({ action: answer.action, call_id: pending.call_id });
          continue;
        }
        let value: Schema["JsonValue"];
        try {
          value =
            answer.action === "respond" && !answer.structured
              ? answer.value
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
    onSuccess: (receipt) => {
      void invalidateConversation(
        cache,
        workspace.id,
        {
          sessionId: run.session_id,
          threadId: thread.id,
          runId: run.id,
        },
        {
          sessionId: receipt.session_id,
          threadId: receipt.thread_id,
          runId: receipt.run_id,
        },
      );
      navigate(runPath(workspace.id, receipt));
    },
  });
  function change(
    id: string,
    answer: { action: string; value: string; structured?: boolean },
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
            return (
              <div key={action.call_id} className={styles.pendingAction}>
                <strong>{action.tool_name ?? action.call_id}</strong>
                <small>{t(action.kind)}</small>
                {action.presentation != null && (
                  <details>
                    <summary>{t("Request details")}</summary>
                    <JsonView value={action.presentation} />
                  </details>
                )}
                {action.kind === "approval" ? (
                  <div className={styles.approvalChoices}>
                    {["approve", "reject"].map((choice) => (
                      <Button
                        key={choice}
                        type="button"
                        variant={
                          answer.action === choice ? "primary" : "secondary"
                        }
                        aria-pressed={answer.action === choice}
                        onClick={() =>
                          change(action.call_id, { ...answer, action: choice })
                        }
                      >
                        {t(choice === "approve" ? "Approve" : "Reject")}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <Select
                    label={t("Response")}
                    placeholder={t("Choose a response")}
                    value={answer.action}
                    onValueChange={(value) =>
                      change(action.call_id, { ...answer, action: value })
                    }
                    options={
                      action.kind === "approval"
                        ? [
                            { value: "approve", label: t("Approve") },
                            { value: "reject", label: t("Reject") },
                          ]
                        : [
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
                          ]
                    }
                  />
                )}
                {["complete", "respond"].includes(answer.action) && (
                  <>
                    {answer.action === "respond" && (
                      <Checkbox
                        label={t("Structured response")}
                        checked={!!answer.structured}
                        onCheckedChange={(checked) =>
                          change(action.call_id, {
                            ...answer,
                            structured: checked === true,
                          })
                        }
                      />
                    )}
                    <TextArea
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
            variant="primary"
            loading={mutation.isPending}
            disabled={
              !actions.every((action) => !!answers[action.call_id]?.action)
            }
          >
            {t("Submit responses")}
          </Button>
        </fieldset>
        <ErrorNotice error={mutation.error} />
      </form>
    </section>
  );
}
