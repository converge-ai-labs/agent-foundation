import type * as Y from "yjs";

// Opaque identity, never the visible filename/label. Plain clipboard text cannot
// recreate an attachment. The registry survives deletion so native undo works.
export const inlinePattern = /\ufffc(inline-[0-9a-f-]{36})\ufffc/g;
export const attachmentToken = (key: string) => `\ufffc${key}\ufffc`;
export const isReadyAttachment = (id: string | undefined): id is string =>
  !!id && id !== "pending" && id !== "failed";
export type AttachmentSelection = {
  key: string;
  id: string | undefined;
  from?: number;
  to?: number;
};
export function attachmentSelections(doc: Y.Doc): AttachmentSelection[] {
  const registry = doc.getMap<string>("attachments");
  const inline = [
    ...doc.getText("text").toString().matchAll(inlinePattern),
  ].map((match) => ({
    key: match[1],
    id: registry.get(match[1]),
    from: match.index,
    to: match.index + match[0].length,
  }));
  return [
    ...inline,
    ...[...registry.keys()]
      .filter((key) => !key.startsWith("inline-"))
      .sort()
      .map((key) => ({ key, id: registry.get(key) })),
  ];
}
export type OrderedInputPart = string | { attachment_id: string };
export function orderedInput(doc: Y.Doc): OrderedInputPart[] {
  const text = doc.getText("text").toString();
  const parts: OrderedInputPart[] = [];
  let offset = 0;
  for (const selection of attachmentSelections(doc)) {
    if (!isReadyAttachment(selection.id))
      throw new Error(
        "Finish, retry, or remove incomplete attachments before sending.",
      );
    if (selection.from !== undefined) {
      if (selection.from > offset)
        parts.push(text.slice(offset, selection.from));
      offset = selection.to!;
    } else if (offset < text.length) {
      parts.push(text.slice(offset));
      offset = text.length;
    }
    parts.push({ attachment_id: selection.id });
  }
  if (offset < text.length) parts.push(text.slice(offset));
  return parts;
}
