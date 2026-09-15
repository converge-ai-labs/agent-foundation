import type { Schema } from "../transport/client";
import { MessageText } from "./message-text";
import styles from "./conversation.module.css";

export function commentReference(
  attachment?: Pick<Schema<"ThreadAttachment">, "source" | "comment"> | null,
) {
  const source = attachment?.source;
  return source && "comment_id" in source
    ? { ...source, ...attachment?.comment }
    : undefined;
}

/** Read the captured envelope, never the current mutable comment. */
function capturedComment(text: string) {
  const firstLine = text.indexOf("\n");
  const original =
    "\n\nReferenced assistant output (complete original text):\n";
  const boundary = text.indexOf(original, firstLine + 1);
  if (firstLine < 0 || boundary < 0) return undefined;
  try {
    const value: unknown = JSON.parse(text.slice(firstLine + 1, boundary));
    if (
      typeof value !== "object" ||
      value === null ||
      !("body" in value) ||
      typeof value.body !== "string"
    )
      return undefined;
    return { body: value.body, output: text.slice(boundary + original.length) };
  } catch {
    return undefined;
  }
}

export function CommentReferenceContent({
  source,
  text,
}: {
  source: NonNullable<ReturnType<typeof commentReference>>;
  text: string;
}) {
  const captured = capturedComment(text);
  return (
    <div className={styles.form}>
      <small>
        Comment{source.author ? ` by ${source.author}` : ""} · captured version{" "}
        {source.version ?? 1}
      </small>
      <p className={styles.commentNotice}>
        This copy stays unchanged if the original comment is edited or deleted.
      </p>
      {captured ? (
        <>
          <MessageText text={captured.body} />
          <details>
            <summary>Referenced response · complete original</summary>
            <MessageText text={captured.output} />
          </details>
          <details>
            <summary>Exact content sent to the agent</summary>
            <pre className={styles.code}>{text}</pre>
          </details>
        </>
      ) : (
        <pre className={styles.code}>{text}</pre>
      )}
    </div>
  );
}
