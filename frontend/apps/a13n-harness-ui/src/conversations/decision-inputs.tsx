import { useState } from "react";
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
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [argumentsText, setArgumentsText] = useState(() =>
    JSON.stringify(
      argumentsObject(request.arguments) ?? request.arguments,
      null,
      2,
    ),
  );
  const [error, setError] = useState("");
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
      setError("Enter a valid JSON object for replacement arguments.");
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
  return (
    <section
      className={styles.request}
      aria-label={`Approval for ${request.tool_name}`}
    >
      <DecisionEvidence request={request} />
      {!onSubmit && (
        <ChoiceField
          label="Approval"
          value={choice}
          onValueChange={(value) => update(value)}
          options={[
            { value: "", label: "Choose a response" },
            ...(canApprove
              ? [{ value: "approve", label: "Approve once" }]
              : []),
            ...(canEdit
              ? [{ value: "override", label: "Approve with edited arguments" }]
              : []),
            { value: "deny", label: "Deny" },
          ]}
        />
      )}
      {(onSubmit || choice === "deny") && (
        <FormField label="Reason (optional)">
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
      )}
      {choice === "override" && (
        <JsonEditor
          label="Replacement arguments (JSON object)"
          value={argumentsText}
          error={error}
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
            {choice === "override"
              ? "Approve with edited arguments"
              : "Approve once"}
          </Button>
          <Button type="button" variant="outline" onClick={() => send("deny")}>
            Deny
          </Button>
          {canEdit && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => update(choice === "override" ? "" : "override")}
            >
              {choice === "override"
                ? "Keep original arguments"
                : "Edit arguments"}
            </Button>
          )}
        </div>
      )}
      <p className={styles.hint}>
        Approval applies only to this request. Deny with a reason to ask the
        Agent for a different action.
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
  const [choice, setChoice] = useState("");
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
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
        if (result.trim()) setError("Enter a valid JSON result.");
        onChange(undefined);
      }
    } else onChange(undefined);
  }
  return (
    <section
      className={styles.request}
      aria-label={`External result for ${request.tool_name}`}
    >
      <DecisionEvidence request={request} />
      <p>
        Provide the actual tool result. This does not execute the tool in your
        browser.
      </p>
      <ChoiceField
        label="External result"
        value={choice}
        onValueChange={(value) => update(value)}
        options={[
          { value: "", label: "Choose a response" },
          { value: "result", label: "Provide a result" },
          { value: "deny", label: "Deny" },
        ]}
      />
      {choice === "result" && (
        <JsonEditor
          label="Result (JSON)"
          value={content}
          error={error}
          onChange={(value) => {
            setContent(value);
            update(choice, value);
          }}
        />
      )}
      {choice === "deny" && (
        <FormField label="Reason (optional)">
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
