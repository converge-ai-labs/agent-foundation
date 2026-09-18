import { useState } from "react";
import { Badge, Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./decisions.module.css";

type Request = Schema<"ApprovalRequestView"> | Schema<"ExternalRequestView">;
export function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}
export function argumentsObject(value: unknown) {
  if (typeof value === "string") {
    try {
      return record(JSON.parse(value));
    } catch {
      return undefined;
    }
  }
  return record(value);
}
const text = (value: unknown) =>
  typeof value === "string" && value.trim() ? value : undefined;

export function decisionEvidence(request: Request) {
  const metadata = request.metadata ?? {};
  const approval = record(metadata["a13n.harness.tool-approval"]);
  const shell =
    request.kind === "approval" &&
    approval?.tool_id === "environment.shell_exec";
  const presentation = record(metadata["a13n.harness.approval-presentation"]);
  const review = shell
    ? record(metadata["a13n.harness.tool-review"])
    : undefined;
  const risk = text(review?.risk) ?? text(presentation?.risk);
  const reason =
    text(review?.reason) ?? text(metadata.reason) ?? text(presentation?.reason);
  const context = Object.fromEntries(
    Object.entries(metadata).filter(
      ([key]) =>
        ![
          "a13n.harness.tool-approval",
          "a13n.harness.tool-review",
          "a13n.harness.approval-presentation",
          "reason",
          "a13n.harness.invocation-policy",
          "a13n.harness.native-tool-approval",
        ].includes(key),
    ),
  );
  return { shell, risk, reason, target: text(presentation?.target), context };
}

export function DecisionEvidence({ request }: { request: Request }) {
  const evidence = decisionEvidence(request);
  const args = argumentsObject(request.arguments);
  const command = evidence.shell ? text(args?.command) : undefined;
  // Never include shell environment values in a raw-argument fallback.
  const safeArgs = args ? { ...args } : undefined;
  const environment = record(safeArgs?.environment);
  if (safeArgs && environment) safeArgs.environment = Object.keys(environment);
  const rest = safeArgs ? { ...safeArgs } : undefined;
  if (rest && command) {
    delete rest.command;
    delete rest.cwd;
    delete rest.alias;
    delete rest.environment;
  }
  return (
    <div className={styles.evidence}>
      <div className={styles.heading}>
        <h3>
          {evidence.shell
            ? "Shell command approval"
            : request.kind === "approval"
              ? "Tool approval"
              : "External result required"}
        </h3>
        {(evidence.risk || evidence.shell) && (
          <Badge
            variant={
              evidence.risk === "high" || evidence.risk === "extra_high"
                ? "destructive"
                : "secondary"
            }
          >
            {evidence.risk
              ? `Risk: ${evidence.risk.replaceAll("_", " ")}`
              : "Risk assessment unavailable"}
          </Badge>
        )}
      </div>
      <span className={styles.tool}>{request.tool_name}</span>
      {evidence.reason && <p className={styles.reason}>{evidence.reason}</p>}
      {evidence.target && <p>Target: {evidence.target}</p>}
      {request.metadata_omitted && (
        <p role="note">Some review context was omitted by the server.</p>
      )}
      {request.arguments_omitted ? (
        <p role="note">
          Arguments omitted by the server.
          {request.kind === "approval" &&
            " Approval is unavailable because the complete request cannot be inspected."}
        </p>
      ) : command ? (
        <>
          <div className={styles.heading}>
            <span>Command</span>
            <CopyCommand command={command} />
          </div>
          <pre className={styles.code}>
            <code>{command}</code>
          </pre>
          <dl className={styles.context}>
            <dt>Working directory</dt>
            <dd>{text(args?.cwd) ?? "Not specified"}</dd>
            {text(args?.alias) && (
              <>
                <dt>Environment mount</dt>
                <dd>{text(args?.alias)}</dd>
              </>
            )}
            {environment && Object.keys(environment).length > 0 && (
              <>
                <dt>Environment variables</dt>
                <dd>{Object.keys(environment).join(", ")} (values hidden)</dd>
              </>
            )}
          </dl>
          {rest && Object.keys(rest).length > 0 && (
            <details>
              <summary>Other execution parameters</summary>
              <pre className={styles.code}>{JSON.stringify(rest, null, 2)}</pre>
            </details>
          )}
        </>
      ) : (
        <pre className={styles.code}>
          {typeof request.arguments === "string" && !args
            ? request.arguments
            : JSON.stringify(safeArgs ?? args ?? request.arguments, null, 2)}
        </pre>
      )}
      {Object.keys(evidence.context).length > 0 && (
        <details>
          <summary>Additional context</summary>
          <pre className={styles.code}>
            {JSON.stringify(evidence.context, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [status, setStatus] = useState("");
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      onClick={() => {
        void (async () => {
          try {
            await navigator.clipboard.writeText(command);
            setStatus("Copied");
          } catch {
            setStatus("Copy failed");
          }
        })();
      }}
    >
      {status || "Copy command"}
    </Button>
  );
}
