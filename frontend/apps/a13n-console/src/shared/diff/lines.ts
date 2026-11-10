import { diffWordsWithSpace } from "diff";

export interface DiffLine {
  number: number;
  text: string;
  noNewline?: boolean;
  parts?: { text: string; changed: boolean }[];
}
export interface DiffRow {
  before?: DiffLine;
  after?: DiffLine;
  context?: boolean;
}
export interface DiffSection {
  before: number;
  after: number;
  rows: DiffRow[];
}

/** Read the available display records, including a bounded, incomplete hunk.
 * parsePatch requires complete hunk counts, which previews cannot guarantee. */
export function diffSections(hunks: readonly string[]): DiffSection[] {
  const sections: DiffSection[] = [];
  let section: DiffSection | undefined;
  let before = 0,
    after = 0;
  let removed: DiffLine[] = [],
    added: DiffLine[] = [];
  let last: DiffLine[] = [];
  function flush() {
    for (let i = 0; i < Math.max(removed.length, added.length); i++) {
      const left = removed[i],
        right = added[i];
      if (left && right) {
        // Bound word matching for unrelated or very long changed lines.
        const parts = diffWordsWithSpace(left.text, right.text, {
          maxEditLength: 256,
        });
        if (parts) {
          left.parts = parts
            .filter((part) => !part.added)
            .map((part) => ({ text: part.value, changed: part.removed }));
          right.parts = parts
            .filter((part) => !part.removed)
            .map((part) => ({ text: part.value, changed: part.added }));
        }
      }
      section?.rows.push({ before: left, after: right });
    }
    removed = [];
    added = [];
  }
  for (const hunk of hunks) {
    for (const record of hunk.split("\n")) {
      const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(record);
      if (header) {
        flush();
        before = Number(header[1]);
        after = Number(header[2]);
        section = { before, after, rows: [] };
        sections.push(section);
        last = [];
      } else if (section && record.startsWith("\\ No newline")) {
        for (const line of last) line.noNewline = true;
      } else if (section && record.startsWith("-")) {
        const line = { number: before++, text: record.slice(1) };
        removed.push(line);
        last = [line];
      } else if (section && record.startsWith("+")) {
        const line = { number: after++, text: record.slice(1) };
        added.push(line);
        last = [line];
      } else if (section && record.startsWith(" ")) {
        flush();
        const left = { number: before++, text: record.slice(1) };
        const right = { number: after++, text: record.slice(1) };
        section.rows.push({ before: left, after: right, context: true });
        last = [left, right];
      }
    }
    flush();
    section = undefined;
  }
  return sections;
}
