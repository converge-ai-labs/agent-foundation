export type CitationSource = {
  title?: string | null;
  excerpts?: readonly string[];
} & (
  | { kind: "web"; url: string }
  | { kind: "document"; document_id?: string | null }
);

export type CitationAnchor = {
  kind: "content" | "marker";
  start: number;
  end: number;
};

export type Citation = {
  sources: readonly CitationSource[];
  anchor?: CitationAnchor | null;
};

export type CitedSegment = { text: string; citations?: readonly Citation[] };
export type NumberedSource = { number: number; source: CitationSource };
type Token = { numbers: number[]; original: string };

const maxCitationBytes = 24 * 1024;
const encoder = new TextEncoder();
const unresolved = /\uE200(?:cite|filecite)\uE202[^\uE200\uE201]+\uE201/gu;
const marker =
  /^(?:\uE200[^\uE200\uE201]+\uE201|\(\[[^\n]+\]\([^\n]+\)\)|(?:\[\d+\]|\[\[\d+\]\])(?:\([^\n]+\))?)$/u;

function object(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object";
}

function isSource(value: unknown): value is CitationSource {
  return (
    object(value) &&
    (value.title == null || typeof value.title === "string") &&
    (value.excerpts === undefined ||
      (Array.isArray(value.excerpts) &&
        value.excerpts.every((part) => typeof part === "string"))) &&
    ((value.kind === "web" && typeof value.url === "string") ||
      (value.kind === "document" &&
        (value.document_id == null || typeof value.document_id === "string")))
  );
}

export function isCitation(value: unknown): value is Citation {
  if (
    !object(value) ||
    !Array.isArray(value.sources) ||
    !value.sources.length ||
    !value.sources.every(isSource)
  )
    return false;
  const anchor = value.anchor;
  return (
    anchor == null ||
    (object(anchor) &&
      (anchor.kind === "content" || anchor.kind === "marker") &&
      typeof anchor.start === "number" &&
      Number.isSafeInteger(anchor.start) &&
      anchor.start >= 0 &&
      typeof anchor.end === "number" &&
      Number.isSafeInteger(anchor.end) &&
      anchor.end > anchor.start)
  );
}

export function readCitations(value: unknown): Citation[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const citations = value.filter(isCitation);
  return citations.length ? citations : undefined;
}

export function mergeCitations(
  citations: readonly Citation[],
  text: string,
): Citation[] {
  const length = [...text].length;
  const merged = new Map<string, Citation>();
  let size = 2;
  for (const citation of citations) {
    const anchor =
      citation.anchor && citation.anchor.end <= length ? citation.anchor : null;
    const key = JSON.stringify({
      sources: citation.sources.map((source) => ({
        kind: source.kind,
        ...(source.kind === "web"
          ? { url: source.url }
          : { document_id: source.document_id ?? null }),
        title: source.title ?? null,
        excerpts: source.excerpts ?? [],
      })),
      anchor: anchor && {
        kind: anchor.kind,
        start: anchor.start,
        end: anchor.end,
      },
    });
    if (merged.has(key)) continue;
    size += encoder.encode(key).length + (merged.size > 0 ? 1 : 0);
    if (size > maxCitationBytes) break;
    merged.set(key, { sources: citation.sources, anchor });
    if (merged.size === 64) break;
  }
  return [...merged.values()];
}

export function codePointIndexToUtf16(text: string, index: number): number {
  let offset = 0;
  for (const char of text) {
    if (index-- <= 0) break;
    offset += char.length;
  }
  return offset;
}

export function mergeCitedSegments(
  segments: readonly CitedSegment[],
  separator = "",
): CitedSegment {
  const citations: Citation[] = [];
  let offset = 0;
  const gap = [...separator].length;
  for (const segment of segments) {
    const length = [...segment.text].length;
    for (const { sources, anchor } of segment.citations ?? []) {
      citations.push({
        sources,
        anchor:
          anchor && anchor.end <= length
            ? {
                ...anchor,
                start: anchor.start + offset,
                end: anchor.end + offset,
              }
            : null,
      });
    }
    offset += length + gap;
  }
  return {
    text: segments.map((segment) => segment.text).join(separator),
    citations,
  };
}

export function safeCitationUrl(value: string): URL | undefined {
  if (!/^https?:\/\//i.test(value) || /[\s\\]/u.test(value)) return;
  try {
    const url = new URL(value);
    if (
      (url.protocol === "http:" || url.protocol === "https:") &&
      url.hostname &&
      !url.username &&
      !url.password
    )
      return url;
  } catch {
    // Unparseable locators remain readable text.
  }
}

function sourceKey(source: CitationSource): string {
  return source.kind === "web"
    ? `web:${source.url}`
    : `document:${source.document_id || source.title || JSON.stringify(source.excerpts)}`;
}

export function stripUnresolvedCitations(text: string): string {
  return text.replace(unresolved, "");
}

export function prepareCitations({ text, citations }: CitedSegment) {
  const sources = new Map<string, NumberedSource>();
  const edits = new Map<
    string,
    { start: number; end: number; numbers: Set<number> }
  >();
  const count = citations?.some((citation) => citation.anchor)
    ? [...text].length
    : 0;
  const offsets = new Map<number, number>();
  function utf16(index: number) {
    const known = offsets.get(index);
    if (known !== undefined) return known;
    const offset = codePointIndexToUtf16(text, index);
    offsets.set(index, offset);
    return offset;
  }
  const occupied = new Set(
    Array.from(text.matchAll(/\uE210(\d+):/gu), (match) => match[1]),
  );
  let tokenId = 0;
  while (occupied.has(String(tokenId))) tokenId++;
  const prefix = `\uE210${tokenId}:`;
  for (const { anchor, sources: references } of citations ?? []) {
    const numbers = references.map((source) => {
      const key = sourceKey(source);
      let entry = sources.get(key);
      if (!entry) {
        entry = { number: sources.size + 1, source };
        sources.set(key, entry);
      }
      return entry.number;
    });
    if (!anchor || anchor.end > count) continue;
    let [start, end] = [utf16(anchor.start), utf16(anchor.end)];
    const selected = text.slice(start, end);
    if (anchor.kind === "content") {
      end -= selected.length - selected.replace(/[\r\n]+$/u, "").length;
      start = end;
    } else if (!marker.test(selected)) {
      start = end; // Keep a descriptive Markdown link or prose intact.
    }
    const key = `${start}:${end}`;
    let edit = edits.get(key);
    if (!edit) {
      edit = { start, end, numbers: new Set() };
      edits.set(key, edit);
    }
    numbers.forEach((number) => edit.numbers.add(number));
  }
  const tokens: Token[] = [];
  let consumed = 0,
    markdown = "";
  for (const { start, end, numbers } of [...edits.values()].sort(
    (a, b) => a.start - b.start || b.end - a.end,
  )) {
    if (start < consumed) continue;
    markdown += text.slice(consumed, start) + `${prefix}${tokens.length}\uE211`;
    tokens.push({ numbers: [...numbers], original: text.slice(start, end) });
    consumed = end;
  }
  markdown += text.slice(consumed);
  return { markdown, sources: [...sources.values()], tokens, prefix };
}

export type PreparedCitations = ReturnType<typeof prepareCitations>;

export function citationTokens(prefix: string): RegExp {
  return new RegExp(`${prefix}(\\d+)\\uE211|${unresolved.source}`, "gu");
}

export function formatCitedCopyText(
  segment: CitedSegment,
  unavailable = "[source unavailable]",
): string {
  const { markdown, sources, tokens, prefix } = prepareCitations(segment);
  const body = markdown.replace(
    citationTokens(prefix),
    (_, index: string | undefined) => {
      const token = index === undefined ? undefined : tokens[Number(index)];
      return token
        ? token.numbers.map((number) => `[${number}]`).join("")
        : unavailable;
    },
  );
  if (!sources.length) return body;
  return `${body.trimEnd()}\n\n${sources
    .map(({ number, source }) => {
      const locator = source.kind === "web" ? source.url : source.document_id;
      const label =
        [...new Set([source.title, locator].filter(Boolean))].join(" — ") ||
        source.excerpts?.[0] ||
        "Document";
      return `[${number}] ${stripUnresolvedCitations(label)}`;
    })
    .join("\n")}`;
}
