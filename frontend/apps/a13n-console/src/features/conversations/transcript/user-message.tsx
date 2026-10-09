import { DisclosureSection } from "a13n-ui";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../shared/forms";
import { MarkdownContent } from "../../../shared/markdown";
import { MessageAuthor } from "../message-author";
import { StoredContent } from "../stored-content";
import { inputText } from "../input";
import { isRecord } from "../../../service-client";
import type { RunRequest } from "../request";
import { AssetAttachment, AttachmentChip } from "./attachment";
import styles from "./transcript.module.css";

/** What the Run was asked to do: a right-aligned bubble under its caption. */
export function UserMessage({
  request,
  entryId,
  principalId,
}: {
  request: RunRequest;
  entryId?: string | null;
  principalId?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.userTurn}>
      {request.kind === "message" ? (
        <MessageAuthor entryId={entryId} principalId={principalId} />
      ) : (
        <span className={styles.userCaption}>{requestLabel(request, t)}</span>
      )}
      <article className={styles.userMessage}>
        <RequestContent request={request} />
      </article>
    </div>
  );
}

/** The words a resolved request is worth; the decision itself is `request.ts`. */
export function requestLabel(request: RunRequest, t: TFunction): string {
  switch (request.kind) {
    case "feedback":
      return t("Feedback");
    case "subagent_result":
      return request.subagent
        ? t("Subagent result · {{name}}", { name: request.subagent })
        : t("Subagent result");
    case "delegated_task":
      return t("Delegated task");
    default:
      return t("User");
  }
}

/** What the request carried, rendered the same way at either level. */
export function RequestContent({ request }: { request: RunRequest }) {
  const { t } = useTranslation();
  if (request.kind === "subagent_result")
    return (
      <>
        {/* The payload is the child's own reply, so it reads as prose. */}
        <MarkdownContent text={request.text} />
        {request.status && (
          <p className={styles.requestNote}>
            {t(`state.${request.status}`, { defaultValue: request.status })}
          </p>
        )}
      </>
    );
  if (request.kind === "delegated_task")
    return (
      <>
        <div className={styles.prose}>{request.text}</div>
        {request.parentTask && (
          <p className={styles.requestNote}>
            {t("From: {{task}}", { task: request.parentTask })}
          </p>
        )}
      </>
    );
  return <InputContent input={request.input} fallback={request.text} />;
}

/** Guidance arrived while the agent worked: the same bubble, drawn open. */
export function GuidanceMessage({
  text,
  itemId,
  sourceId,
  steeringSource,
}: {
  text: string;
  itemId?: string;
  sourceId?: string;
  steeringSource?: string | null;
}) {
  const { t } = useTranslation();
  if (!text.trim()) return null;
  return (
    <div className={styles.userTurn}>
      {!steeringSource && <MessageAuthor entryId={sourceId} />}
      <article className={`${styles.userMessage} ${styles.guidanceMessage}`}>
        <span className={styles.guidanceLabel}>{t("Guidance")}</span>
        <div className={styles.prose}>
          {itemId ? (
            <StoredContent itemId={itemId} field="text" value={text} />
          ) : (
            text
          )}
        </div>
      </article>
    </div>
  );
}

export function InputContent({
  input,
  fallback,
}: {
  input: unknown;
  fallback?: string | null;
}) {
  const { t } = useTranslation();
  if (isRecord(input) && Array.isArray(input.answers))
    return (
      <div className={styles.resolutions}>
        {input.answers.map((answer, index) =>
          isRecord(answer) ? (
            <section key={index}>
              <strong>{String(answer.tool_call_id ?? t("Response"))}</strong> ·{" "}
              {t(String(answer.action ?? "Response"))}
              {answer.result !== undefined && (
                <JsonView value={answer.result} />
              )}
              {answer.reason != null && <JsonView value={answer.reason} />}
            </section>
          ) : null,
        )}
      </div>
    );
  if (!isRecord(input) || !Array.isArray(input.content))
    return fallback ? (
      <div className={styles.prose}>{fallback}</div>
    ) : (
      <JsonView value={input} />
    );
  const parts = input.content.filter(isRecord);
  const attachments = parts.flatMap((part, index) =>
    part.type === "asset" && typeof part.asset_id === "string"
      ? [<AssetAttachment key={index} assetId={part.asset_id} />]
      : part.type === "url" && typeof part.url === "string"
        ? [<AttachmentChip key={index} label={part.url} />]
        : [],
  );
  const structured = parts.filter((part) => part.type === "json");
  return (
    <>
      <div className={styles.prose}>{inputText(input, fallback)}</div>
      {!!attachments.length && (
        <div className={styles.attachments}>{attachments}</div>
      )}
      {structured.map((part, index) => (
        <DisclosureSection
          key={index}
          className={styles.inlineDisclosure}
          title={<>{t("Structured input")}</>}
        >
          <JsonView value={part.value} />
        </DisclosureSection>
      ))}
    </>
  );
}
