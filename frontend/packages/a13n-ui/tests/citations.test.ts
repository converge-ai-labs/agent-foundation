import { describe, expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import type { fromMarkdown } from "mdast-util-from-markdown";
import {
  codePointIndexToUtf16,
  formatCitedCopyText,
  mergeCitations,
  mergeCitedSegments,
  prepareCitations,
  safeCitationUrl,
  stripUnresolvedCitations,
  type Citation,
  type CitationSource,
} from "../src/citations";
import { remarkCitations } from "../src/remark-citations";

const raw = "\uE200cite\uE202turn0view0\uE201";
const file = "\uE200filecite\uE202file0\uE201";
const web: CitationSource = {
  kind: "web",
  url: "https://example.com/spec",
  title: "Specification",
};
const doc: CitationSource = {
  kind: "document",
  document_id: "doc_1",
  title: "Report",
  excerpts: ["Passage"],
};
const length = (text: string) => [...text].length;
const citation = (
  kind: "marker" | "content",
  before: string,
  range: string,
  source: CitationSource = web,
): Citation => ({
  anchor: { kind, start: length(before), end: length(before + range) },
  sources: [source],
});

function parse(prepared: ReturnType<typeof prepareCitations>) {
  const parser = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(() => remarkCitations(prepared));
  return parser.runSync(parser.parse(prepared.markdown)) as ReturnType<
    typeof fromMarkdown
  >;
}

function numberInAst(number: string) {
  return {
    type: "text",
    value: "",
    data: {
      hName: "a13n-citation",
      hProperties: { dataCitationNumbers: number },
    },
  };
}

describe("reply citations", () => {
  it.each([
    raw,
    "\uE200provider-marker\uE201",
    "([Example](https://example.com/spec))",
    "[[7]](https://example.com/spec)",
    "[42]",
  ])(
    "replaces standalone marker %s at Unicode code-point offsets",
    (marker) => {
      const before = "🙂 claim ";
      const segment = {
        text: before + marker + ".",
        citations: [citation("marker", before, marker)],
      };
      const prepared = prepareCitations(segment);
      expect(codePointIndexToUtf16(before, length(before))).toBe(before.length);
      expect(parse(prepared).children[0]).toMatchObject({
        children: [{ value: before }, numberInAst("1"), { value: "." }],
      });
      expect(formatCitedCopyText(segment)).toBe(
        "🙂 claim [1].\n\n[1] Specification — https://example.com/spec",
      );
    },
  );

  it("preserves explanatory prose links and puts null anchors only in sources", () => {
    const link = "[the full report](https://example.com/spec)";
    const segment = {
      text: "Read " + link + " today",
      citations: [citation("marker", "Read ", link), { sources: [doc] }],
    };
    const prepared = prepareCitations(segment);
    expect(parse(prepared).children[0]).toMatchObject({
      children: [
        { value: "Read " },
        {
          type: "link",
          url: web.url,
          children: [{ value: "the full report" }],
        },
        numberInAst("1"),
        { value: " today" },
      ],
    });
    expect(
      prepared.sources.map(({ number, source }) => [number, source.kind]),
    ).toEqual([
      [1, "web"],
      [2, "document"],
    ]);
    expect(formatCitedCopyText(segment)).toContain(`Read ${link}[1] today`);
  });

  it("keeps headings, list items, and GFM table claims intact", () => {
    const prefix = "# Title\r\n\r\n- Claim\n\n| Col |\n| --- |\n| ";
    const segment = {
      text: prefix + "Result |\n",
      citations: [
        citation("content", "# ", "Title\r\n"),
        citation("content", "# Title\r\n\r\n- ", "Claim\n"),
        citation("content", prefix, "Result"),
      ],
    };
    const prepared = prepareCitations(segment);
    expect(parse(prepared).children).toMatchObject([
      { type: "heading", children: [{ value: "Title" }, numberInAst("1")] },
      {
        type: "list",
        children: [
          { children: [{ children: [{ value: "Claim" }, numberInAst("1")] }] },
        ],
      },
      {
        type: "table",
        children: [
          { type: "tableRow" },
          {
            type: "tableRow",
            children: [{ children: [{ value: "Result" }, numberInAst("1")] }],
          },
        ],
      },
    ]);
    expect(prepared.sources).toEqual([{ number: 1, source: web }]);
  });

  it("detects unresolved prose without interpreting literal code or a user sentinel", () => {
    const lookalike = "\uE2100:0\uE211";
    const prefix = `${lookalike} ${raw} \`${file}\`\n\n\`\`\`text\n`;
    const markdown = `${prefix}[6]\n${raw}\n\`\`\``;
    const tree = parse(
      prepareCitations({
        text: markdown,
        citations: [citation("marker", prefix, "[6]")],
      }),
    );
    expect(tree.children).toMatchObject([
      {
        type: "paragraph",
        children: [
          { value: `${lookalike} ` },
          numberInAst(""),
          { value: " " },
          { type: "inlineCode", value: file },
        ],
      },
      { type: "code", value: `[6]\n${raw}` },
    ]);
    expect(stripUnresolvedCitations(`Answer${raw}${file}`)).toBe("Answer");
    expect(formatCitedCopyText({ text: `Answer${raw}` })).toBe(
      "Answer[source unavailable]",
    );
  });

  it("shifts adjacent Anthropic and Bedrock ranges in one reply and numbers each source once", () => {
    const merged = mergeCitedSegments([
      { text: "🙂 First", citations: [citation("content", "", "🙂 First")] },
      {
        text: "; second",
        citations: [
          citation("content", "; ", "second"),
          citation("content", "; ", "second", doc),
        ],
      },
    ]);
    expect(merged.citations?.[1]?.anchor).toEqual({
      kind: "content",
      start: 9,
      end: 15,
    });
    expect(formatCitedCopyText(merged)).toBe(
      "🙂 First[1]; second[1][2]\n\n[1] Specification — https://example.com/spec\n[2] Report — doc_1",
    );
  });

  it("fits a bounded prefix in replay and keeps sources for anchors outside available text", () => {
    const candidates = Array.from({ length: 20 }, (_, i): Citation => ({
      anchor: { kind: "content", start: 1, end: 10 },
      sources: [
        {
          kind: "document",
          document_id: String(i),
          title: null,
          excerpts: ["🙂".repeat(512), "🙂".repeat(512)],
        },
      ],
    }));
    const kept = mergeCitations([candidates[0]!, ...candidates], "Hi");
    expect(kept).toHaveLength(5);
    expect(kept[0]?.anchor).toBeNull();
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,hi",
    "//example.com/",
    "/page",
    "https://a@b.com",
    "https://host\\@evil.com",
  ])("rejects unsafe or nonabsolute locator %s", (url) =>
    expect(safeCitationUrl(url)).toBeUndefined(),
  );
  it("allows explicit HTTP(S) locators", () => {
    expect(safeCitationUrl(web.url)?.hostname).toBe("example.com");
    expect(safeCitationUrl("http://example.com")?.protocol).toBe("http:");
  });
});
