import { stripUnresolvedCitations } from "a13n-ui/citations";

/** One line of plain text that previews a Markdown message. */
export function markdownPreview(text: string, length = 240) {
  return stripUnresolvedCitations(text)
    .replace(/!?(?:\[([^\]]*)\])\([^)]*\)/g, "$1")
    .replace(/^\s*\|?[\s:|-]*-{3,}[\s:|-]*$/gm, "")
    .replace(/^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)/gm, "")
    .replace(/```[^\n]*\n?/g, "")
    .replace(/\*+|__|[`~]/g, "")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, length);
}
