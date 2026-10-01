import type { ContentPart } from "@ag-ui/core";
import { ContentPartSchema } from "@ag-ui/core/schemas";
import type { ReactNode } from "react";

const partsSchema = ContentPartSchema.array();

/** Canonical AG-UI content only; arbitrary tool JSON is not implicitly media. */
export function readContentParts(value: unknown): ContentPart[] | undefined {
  const parsed = partsSchema.safeParse(value);
  return parsed.success ? parsed.data : undefined;
}

/** Public URL media only. Host-authenticated attachments keep their Host renderer. */
export function AguiContent({
  parts,
  renderText = (text) => (
    <p className="whitespace-pre-wrap break-words">{text}</p>
  ),
}: {
  parts: ContentPart[];
  renderText?: (text: string) => ReactNode;
}) {
  return (
    <div className="grid min-w-0 gap-3" data-slot="agui-content">
      {parts.map((part, index) => {
        if (
          part.metadata &&
          typeof part.metadata === "object" &&
          "display" in part.metadata &&
          part.metadata.display === false
        )
          return null;
        if (part.type === "text")
          return <div key={index}>{renderText(part.text)}</div>;
        const source = part.source;
        const url =
          source.type === "url" && /^https?:\/\//i.test(source.value)
            ? source.value
            : undefined;
        if (!url)
          return (
            <p key={index} className="text-sm text-muted-foreground">
              {part.type} preview unavailable
              {source.type === "file" ? ` · ${source.value}` : ""}
            </p>
          );
        if (part.type === "image")
          return (
            <a key={index} href={url} target="_blank" rel="noreferrer">
              <img
                src={url}
                alt="Tool result image"
                loading="lazy"
                referrerPolicy="no-referrer"
                className="max-h-96 max-w-full rounded-lg object-contain"
              />
            </a>
          );
        if (part.type === "audio")
          return (
            <audio
              key={index}
              controls
              preload="none"
              src={url}
              className="max-w-full"
            />
          );
        if (part.type === "video")
          return (
            <video
              key={index}
              controls
              preload="none"
              src={url}
              className="max-h-96 max-w-full rounded-lg"
            />
          );
        return (
          <a
            key={index}
            href={url}
            target="_blank"
            rel="noreferrer"
            className="break-all text-sm underline underline-offset-4"
          >
            Open document{source.mimeType ? ` · ${source.mimeType}` : ""}
          </a>
        );
      })}
    </div>
  );
}
