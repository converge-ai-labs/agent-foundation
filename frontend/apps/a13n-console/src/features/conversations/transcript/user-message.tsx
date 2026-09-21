import { DisclosureSection } from "a13n-ui";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../shared/forms";
import { MarkdownContent } from "../../../shared/markdown";
import { inputText } from "../input";
import { isObject } from "../projection";
import type { RunRequest } from "../request";
import { AssetAttachment, AttachmentChip } from "./attachment";
import styles from "./transcript.module.css";

/** What the Run was asked to do: a right-aligned bubble under its caption. */
export function UserMessage({ request }: { request: RunRequest }) {
  const { t } = useTranslation();
  return (
    <div className={styles.userTurn}>
      {request.kind !== "message" && (
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
    case "continue":
      return t("Continued without feedback");
    case "subagent_result":
      return request.subagent
        ? t("Subagent result · {{name}}", { name: request.subagent })
        : t("Subagent result");
    case "delegated_task":
      return t("Delegated task");
    default:
      return t("You");
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
export function GuidanceMessage({ text }: { text: string }) {
  const { t } = useTranslation();
  if (!text.trim()) return null;
  return (
    <div className={styles.userTurn}>
      <article className={`${styles.userMessage} ${styles.guidanceMessage}`}>
        <span className={styles.guidanceLabel}>{t("Guidance")}</span>
        <div className={styles.prose}>{text}</div>
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
  if (isObject(input) && Array.isArray(input.resolutions))
    return (
      <div className={styles.resolutions}>
        {input.resolutions.map((resolution, index) =>
          isObject(resolution) ? (
            <section key={index}>
              <strong>{String(resolution.call_id ?? t("Response"))}</strong> ·{" "}
              {t(String(resolution.outcome ?? resolution.kind ?? "Response"))}
              {resolution.result !== undefined && (
                <JsonView value={resolution.result} />
              )}
              {resolution.response !== undefined && (
                <JsonView value={resolution.response} />
              )}
            </section>
          ) : null,
        )}
      </div>
    );
  const ordinary =
    isObject(input) && isObject(input.input) ? input.input : input;
  if (!isObject(ordinary) || !Array.isArray(ordinary.content))
    return fallback ? (
      <div className={styles.prose}>{fallback}</div>
    ) : (
      <JsonView value={input} />
    );
  const attachments = ordinary.content.flatMap((block, index) => {
    if (!isObject(block) || block.type !== "binary" || !isObject(block.source))
      return [];
    const filename =
      typeof block.filename === "string" ? block.filename : undefined;
    if (
      block.source.type === "asset" &&
      typeof block.source.asset_id === "string"
    )
      return [
        <AssetAttachment
          key={index}
          assetId={block.source.asset_id}
          filename={filename}
        />,
      ];
    return [
      <AttachmentChip
        key={index}
        label={
          filename ??
          String(
            block.source.type === "url" ? block.source.url : block.source.path,
          )
        }
      />,
    ];
  });
  return (
    <>
      <div className={styles.prose}>{inputText(input, fallback)}</div>
      {!!attachments.length && (
        <div className={styles.attachments}>{attachments}</div>
      )}
      {ordinary.structured_content !== undefined &&
        ordinary.structured_content !== null && (
          <DisclosureSection
            className={styles.inlineDisclosure}
            title={<>{t("Structured input")}</>}
          >
            <JsonView value={ordinary.structured_content} />
          </DisclosureSection>
        )}
    </>
  );
}
