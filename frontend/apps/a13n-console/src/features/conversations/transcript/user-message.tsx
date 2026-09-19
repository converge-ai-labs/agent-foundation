import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../shared/forms";
import { inputText } from "../input";
import { isObject } from "../projection";
import { AssetAttachment, AttachmentChip } from "./attachment";
import styles from "./transcript.module.css";

/** What the person sent: a right-aligned bubble, captioned only when it is feedback. */
export function UserMessage({
  input,
  fallback,
  kind,
}: {
  input: unknown;
  fallback?: string | null;
  kind?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.userTurn}>
      {kind === "feedback" && (
        <span className={styles.userCaption}>{t("Feedback")}</span>
      )}
      <article className={styles.userMessage}>
        <InputContent input={input} fallback={fallback} />
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
