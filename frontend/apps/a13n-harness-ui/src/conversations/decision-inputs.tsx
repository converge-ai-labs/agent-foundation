import { useState } from "react";
import { useTranslation } from "../i18n";
import { Button, ChoiceField, FormField, Textarea } from "a13n-ui";
import type { Schema } from "../transport/client";
import { DecisionEvidence, argumentsObject, record } from "./decision-evidence";
import { QuestionInput } from "./question-input";
import styles from "./decisions.module.css";

export type DecisionResponse =
  Schema<"DecisionResponseBatch">["responses"][number];
type Change = (response: DecisionResponse | undefined) => void;
export function DecisionInput({
  request,
  onChange,
  onSubmit,
}: {
  request: Schema<"DecisionRequestView">;
  onChange: Change;
  onSubmit?: (response: DecisionResponse) => void;
}) {
  switch (request.kind) {
    case "question":
      return <QuestionInput request={request} onChange={onChange} />;
    case "approval":
      return (
        <ApprovalInput
          request={request}
          onChange={onChange}
          onSubmit={onSubmit}
        />
      );
    case "external":
      return <ExternalInput request={request} onChange={onChange} />;
  }
}

function JsonEditor({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <FormField label={label} error={error || undefined}>
      <Textarea
        className={styles.editor}
        rows={5}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
    </FormField>
  );
}

function ApprovalInput({
  request,
  onChange,
  onSubmit,
}: {
  request: Schema<"ApprovalRequestView">;
  onChange: Change;
  onSubmit?: (response: DecisionResponse) => void;
}) {
  const { t } = useTranslation();
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [argumentsText, setArgumentsText] = useState(() =>
    JSON.stringify(
      argumentsObject(request.arguments) ?? request.arguments,
      null,
      2,
    ),
  );
  const [error, setError] = useState<"arguments" | "result" | "">("");
  const canApprove = !request.arguments_omitted;
  const canEdit = canApprove && request.override_allowed === true;
  function response(
    value: string,
    content = argumentsText,
    denial = reason,
  ): DecisionResponse | undefined {
    setError("");
    if (value === "deny")
      return {
        kind: "approval",
        request_id: request.request_id,
        approved: false,
        denial_message: denial.trim() || null,
      };
    if (!canApprove || !["approve", "override"].includes(value)) return;
    if (value === "approve")
      return {
        kind: "approval",
        request_id: request.request_id,
        approved: true,
      };
    if (!canEdit) return;
    try {
      const parsed: unknown = JSON.parse(content);
      if (!record(parsed)) throw new Error("Expected an object");
      return {
        kind: "approval",
        request_id: request.request_id,
        approved: true,
        override_arguments: parsed as Record<string, Schema<"JsonValue">>,
      };
    } catch {
      setError("arguments");
    }
  }
  function update(value: string, content = argumentsText, denial = reason) {
    setChoice(value);
    onChange(response(value, content, denial));
  }
  function send(value: string) {
    const next = response(value);
    if (next) onSubmit?.(next);
  }
  const reasonField = (
    <FormField label={t("Reason (optional)")}>
      <Textarea
        rows={2}
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          if (onSubmit) onChange(undefined);
          else update(choice, argumentsText, event.target.value);
        }}
      />
    </FormField>
  );
  return (
    <section
      className={styles.request}
      aria-label={t("Approval for {{tool}}", { tool: request.tool_name })}
    >
      <DecisionEvidence request={request} />
      {!onSubmit && (
        <ChoiceField
          label={t("Approval")}
          value={choice}
          onValueChange={(value) => update(value)}
          options={[
            { value: "", label: t("Choose a response") },
            ...(canApprove
              ? [{ value: "approve", label: t("Approve once") }]
              : []),
            ...(canEdit
              ? [{ value: "override", label: t("Approve edits") }]
              : []),
            { value: "deny", label: t("Deny") },
          ]}
        />
      )}
      {onSubmit ? (
        <details className={styles.denialReason}>
          <summary>{t("Add a reason for denying")}</summary>
          {reasonField}
        </details>
      ) : choice === "deny" ? (
        reasonField
      ) : null}
      {choice === "override" && (
        <JsonEditor
          label={t("Replacement arguments (JSON object)")}
          value={argumentsText}
          error={
            error === "arguments"
              ? t("Enter a valid JSON object for replacement arguments.")
              : undefined
          }
          onChange={(value) => {
            setArgumentsText(value);
            update("override", value);
          }}
        />
      )}
      {onSubmit && (
        <div className={styles.actions}>
          <Button
            type="button"
            disabled={!canApprove || (choice === "override" && !!error)}
            onClick={() => send(choice === "override" ? "override" : "approve")}
          >
            {choice === "override" ? t("Approve edits") : t("Approve once")}
          </Button>
          <Button type="button" variant="outline" onClick={() => send("deny")}>
            {t("Deny")}
          </Button>
          {canEdit && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => update(choice === "override" ? "" : "override")}
            >
              {choice === "override" ? t("Keep original") : t("Edit arguments")}
            </Button>
          )}
        </div>
      )}
      <p className={styles.hint}>
        {t(
          "Approval is for this request only. To ask for a different action, deny it and add a reason.",
        )}
      </p>
    </section>
  );
}

function ExternalInput({
  request,
  onChange,
}: {
  request: Schema<"ExternalRequestView">;
  onChange: Change;
}) {
  const { t } = useTranslation();
  const [choice, setChoice] = useState("");
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<"arguments" | "result" | "">("");
  function update(value: string, result = content, denial = reason) {
    setChoice(value);
    setError("");
    if (value === "deny") {
      onChange({
        kind: "external",
        request_id: request.request_id,
        denied: true,
        denial_message: denial.trim() || "Denied.",
      });
    } else if (value === "result") {
      try {
        // An empty draft is not an actual result. Explicit JSON null is valid.
        onChange({
          kind: "external",
          request_id: request.request_id,
          result: JSON.parse(result),
        });
      } catch {
        if (result.trim()) setError("result");
        onChange(undefined);
      }
    } else onChange(undefined);
  }
  return (
    <section
      className={styles.request}
      aria-label={t("External result for {{tool}}", {
        tool: request.tool_name,
      })}
    >
      <DecisionEvidence request={request} />
      <p>{t("Enter the tool's result. The tool won't run in your browser.")}</p>
      <ChoiceField
        label={t("External result")}
        value={choice}
        onValueChange={(value) => update(value)}
        options={[
          { value: "", label: t("Choose a response") },
          { value: "result", label: t("Provide a result") },
          { value: "deny", label: t("Deny") },
        ]}
      />
      {choice === "result" && (
        <JsonEditor
          label={t("Result (JSON)")}
          value={content}
          error={
            error === "result" ? t("Enter a valid JSON result.") : undefined
          }
          onChange={(value) => {
            setContent(value);
            update(choice, value);
          }}
        />
      )}
      {choice === "deny" && (
        <FormField label={t("Reason (optional)")}>
          <Textarea
            rows={2}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              update(choice, content, event.target.value);
            }}
          />
        </FormField>
      )}
    </section>
  );
}
