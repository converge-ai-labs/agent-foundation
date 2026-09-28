import { Button, ChoiceField, DisclosureSection, Label, Switch } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import {
  CheckIcon,
  HandPalmIcon,
  QuestionIcon,
  WarningIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { CopyButton } from "../../../shared/identity";
import { JsonView, TextAreaField } from "../../../shared/forms";
import { QuestionResponse, readQuestions } from "./questions";
import styles from "./cards.module.css";

type PendingAction = Schema["PendingCall"] & { category: "approval" | "call" };

function pendingActions(pending: Schema["Pending"]): PendingAction[] {
  return [
    ...pending.approvals.map((call) => ({
      ...call,
      category: "approval" as const,
    })),
    ...pending.calls.map((call) => ({ ...call, category: "call" as const })),
  ];
}

type Answer = {
  action: string;
  value: string;
  structured?: boolean;
  reasonMode?: boolean;
};

/** The run paused and this console cannot answer for the owning application. */
export function PendingRequests({ pending }: { pending: Schema["Pending"] }) {
  const { t } = useTranslation();
  const actions = pendingActions(pending);
  return (
    <div className={styles.cards} aria-label={t("Waiting for the application")}>
      <p className={styles.cardsNote}>
        <span className={styles.cardsNoteTitle}>
          {t("Waiting for the application")}
        </span>
        {t(
          "The agent paused this run for input or approval. It resumes when the host application responds.",
        )}
      </p>
      {actions.map((action) => (
        <ActionCard key={action.tool_call_id} action={action}>
          <DisclosureSection
            className={styles.inlineDisclosure}
            title={<>{t("Request details")}</>}
          >
            <JsonView value={action.presentation ?? action.arguments} />
          </DisclosureSection>
        </ActionCard>
      ))}
    </div>
  );
}

/**
 * Every pending action is answered together; the agent resumes with the set.
 * Questions, approvals and tool results all name their exact pending call.
 * Ordinary inbox messages never resolve this wait.
 */
export function RunFeedback({
  run,
  pending,
  accepted,
  continuation,
}: {
  run: Schema["RunView"];
  pending: Schema["Pending"];
  accepted: (next: Schema["RunView"] | null) => void;
  /** The "continue without feedback" escape, shown beside Submit. */
  continuation?: ReactNode;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const actions = pendingActions(pending);
  const [answers, setAnswers] = useState<Record<string, Answer>>({}),
    [key, setKey] = useState(crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: async () => {
      const resume: Schema["Resume"] = {
        approvals: Object.create(null),
        calls: Object.create(null),
      };
      for (const pending of actions) {
        const answer = answers[pending.tool_call_id];
        if (!answer?.action)
          throw new Error(t("Choose a response for every pending action."));
        if (pending.category === "approval") {
          resume.approvals[pending.tool_call_id] =
            answer.action === "approve"
              ? { action: "approve" }
              : {
                  action: "deny",
                  ...(answer.value.trim()
                    ? { reason: answer.value.trim() }
                    : {}),
                };
          continue;
        }
        if (answer.action === "omit" || answer.action === "failed") {
          const message =
            answer.action === "omit"
              ? "No response was given"
              : answer.value.trim();
          if (!message) throw new Error(t("Enter a failure reason."));
          resume.calls[pending.tool_call_id] = { status: "failed", message };
          continue;
        }
        let value: Schema["JsonValue"];
        if (answer.action === "respond" && !answer.structured) {
          if (!answer.value.trim())
            throw new Error(t("Choose a response for every pending action."));
          value = { response: answer.value.trim() };
        } else {
          try {
            value = JSON.parse(answer.value);
          } catch {
            throw new Error(
              t("Tool results and responses must be valid JSON."),
            );
          }
        }
        resume.calls[pending.tool_call_id] = { status: "returned", value };
      }
      const workspace_id = workspace.id;
      return data(
        await client
          .workspace(workspace_id)
          .POST("/api/v1/runs/{run_id}/resume", {
            params: {
              path: { run_id: run.id },
              header: commandHeaders(key),
            },
            body: resume,
          }),
      );
    },
    onSuccess: (next) => accepted(next),
  });
  function change(id: string, answer: Answer) {
    setAnswers((previous) => ({ ...previous, [id]: answer }));
    setKey(crypto.randomUUID());
  }
  return (
    <form
      className={styles.cards}
      aria-label={t("Waiting for your response")}
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <p className={styles.cardsNote}>
        <span className={styles.cardsNoteTitle}>
          {t("Waiting for your response")}
        </span>
        {t(
          "Review every action before continuing. The agent resumes with this complete set of responses.",
        )}
      </p>
      <fieldset
        disabled={mutation.isPending || !can("run")}
        className={styles.cards}
      >
        {actions.map((action) => {
          const answer = answers[action.tool_call_id] ?? {
            action: "",
            value: "",
          };
          const questions =
            action.category === "call" &&
            action.tool_name === "ask_user_question"
              ? readQuestions(action.arguments)
              : null;
          if (questions)
            return (
              <ActionCard key={action.tool_call_id} action={action}>
                <QuestionResponse
                  questions={questions}
                  onChange={(value) => change(action.tool_call_id, value)}
                />
              </ActionCard>
            );
          const details =
            action.category === "approval"
              ? approvalDetails(action.presentation)
              : null;
          return (
            <ActionCard
              key={action.tool_call_id}
              action={action}
              details={details}
            >
              {details?.target && (
                <div className={styles.cardCommand}>
                  <code>{details.target}</code>
                  <CopyButton
                    value={details.target}
                    iconOnly
                    copyLabel={t("Copy command")}
                  />
                </div>
              )}
              {details && (lowRisk(details.risk) || details.reason) && (
                <dl className={styles.cardFacts}>
                  {/* High risk is already stated in the header badge. */}
                  {lowRisk(details.risk) && (
                    <>
                      <dt>{t("Risk")}</dt>
                      <dd>{t(details.risk!)}</dd>
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
              {!details && (
                <DisclosureSection
                  className={styles.inlineDisclosure}
                  title={<>{t("Request details")}</>}
                >
                  <JsonView value={action.presentation ?? action.arguments} />
                </DisclosureSection>
              )}
              {action.category === "approval" ? (
                <div className={styles.cardFooter}>
                  {(
                    [
                      ["approve", t("Approve once"), "default"],
                      ["reject", t("Deny"), "outline"],
                      ["reject_with_reason", t("Deny with reason"), "ghost"],
                    ] as const
                  ).map(([choice, label, variant]) => {
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
                        variant={pressed ? "secondary" : variant}
                        aria-pressed={pressed}
                        onClick={() =>
                          change(action.tool_call_id, {
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
                        {pressed && <CheckIcon size={13} aria-hidden="true" />}
                        {label}
                      </Button>
                    );
                  })}
                </div>
              ) : (
                <ChoiceField
                  placeholder={t("Choose a response")}
                  value={answer.action}
                  onValueChange={(value) =>
                    change(action.tool_call_id, { ...answer, action: value })
                  }
                  label={t("Response")}
                  hideLabel
                  options={[
                    {
                      value:
                        action.tool_name !== "ask_user_question"
                          ? "complete"
                          : "respond",
                      label: t(
                        action.tool_name !== "ask_user_question"
                          ? "Return tool result"
                          : "Respond",
                      ),
                    },
                    { value: "failed", label: t("Report tool failure") },
                    { value: "omit", label: t("Continue without a response") },
                  ]}
                />
              )}
              {action.category === "approval" &&
                answer.action === "reject" &&
                answer.reasonMode && (
                  <TextAreaField
                    label={t("Denial reason (optional)")}
                    value={answer.value}
                    onChange={(value) =>
                      change(action.tool_call_id, {
                        ...answer,
                        value: value.slice(0, 2000),
                      })
                    }
                    rows={3}
                  />
                )}
              {answer.action === "failed" && (
                <TextAreaField
                  label={t("Failure reason")}
                  value={answer.value}
                  onChange={(value) =>
                    change(action.tool_call_id, {
                      ...answer,
                      value: value.slice(0, 4096),
                    })
                  }
                />
              )}
              {["complete", "respond"].includes(answer.action) && (
                <>
                  {answer.action === "respond" && (
                    <Label className={styles.cardSwitch}>
                      <Switch
                        checked={!!answer.structured}
                        onCheckedChange={(checked) =>
                          change(action.tool_call_id, {
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
                      change(action.tool_call_id, { ...answer, value })
                    }
                  />
                </>
              )}
            </ActionCard>
          );
        })}
        <div className={styles.cardsFooter}>
          {continuation}
          <Button
            type="submit"
            variant="default"
            disabled={
              !actions.every((action) => !!answers[action.tool_call_id]?.action)
            }
            loading={mutation.isPending}
          >
            {t("Submit responses")}
          </Button>
        </div>
      </fieldset>
      <ErrorNotice error={mutation.error} />
    </form>
  );
}

/**
 * One card per waiting action: what is being asked, about what, and how risky
 * the agent judged it.
 */
function ActionCard({
  action,
  details,
  children,
}: {
  action: PendingAction;
  details?: { risk?: string } | null;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const approval = action.category === "approval";
  const question = !approval && action.tool_name === "ask_user_question";
  return (
    <section className={styles.card}>
      <header className={styles.cardHeader}>
        <span className={styles.cardGlyph} aria-hidden="true">
          {approval ? (
            <HandPalmIcon size={14} />
          ) : question ? (
            <QuestionIcon size={14} />
          ) : (
            <WrenchIcon size={14} />
          )}
        </span>
        <h3>
          {approval
            ? t("Approval requested")
            : question
              ? t("The agent has a question")
              : t("Waiting for a tool result")}
        </h3>
        {action.tool_name && (
          <code className={styles.cardTool}>{action.tool_name}</code>
        )}
        {highRisk(details?.risk) && (
          <span className={styles.cardRisk}>
            <WarningIcon size={12} aria-hidden="true" />
            {t("High risk")}
          </span>
        )}
      </header>
      {children}
    </section>
  );
}

const highRisk = (risk?: string) => risk === "high" || risk === "extra_high";
const lowRisk = (risk?: string) => !!risk && !highRisk(risk);

function approvalDetails(
  value: Schema["PendingCall"]["presentation"],
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
