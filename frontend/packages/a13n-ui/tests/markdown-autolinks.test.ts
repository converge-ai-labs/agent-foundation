import { describe, expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";

const parser = unified().use(remarkParse).use(remarkGfm);

function paragraph(markdown: string) {
  const block = parser.parse(markdown).children[0];
  if (block?.type !== "paragraph") throw new Error("Expected a paragraph");
  return block.children;
}

describe.each([
  { prefix: "", origin: "https://example.com", scheme: "", positioned: true },
  { prefix: "见 ", origin: "http://example.com", scheme: "", positioned: true },
  { prefix: "（", origin: "https://example.com", scheme: "", positioned: true },
  { prefix: "见", origin: "https://example.com", scheme: "", positioned: true },
  {
    prefix: "",
    origin: "www.example.com",
    scheme: "http://",
    positioned: true,
  },
  {
    prefix: "另见（",
    origin: "www.example.com",
    scheme: "http://",
    positioned: false,
  },
])("$prefix $origin", ({ prefix, origin, scheme, positioned }) => {
  it.each([..."）】》」』〕〉，。、；：！？’”（【｡｣", "\u3000"])(
    "stops at %j and preserves the following prose",
    (boundary) => {
      const label = `${origin}/wiki/人工智能`;
      const tail = `${boundary}了解更多`;
      const nodes = paragraph(`${prefix}${label}${tail}`);
      const index = nodes.findIndex((node) => node.type === "link");
      const link = nodes[index];
      expect(link).toMatchObject({
        type: "link",
        url: scheme + label,
        children: [{ type: "text", value: label }],
      });
      // Micromark links carry source positions; links from the mdast fallback do not.
      expect(Boolean(link?.position)).toBe(positioned);
      expect(nodes.slice(index + 1)).not.toContainEqual(
        expect.objectContaining({ type: "link" }),
      );
      expect(
        nodes
          .slice(index + 1)
          .map((node) => (node.type === "text" ? node.value : null))
          .join(""),
      ).toBe(tail);
    },
  );

  it.each([
    ["/a", ".）已于"],
    ["/a", ").中文"],
    ["/a", ").中文，继续阅读"],
    ["/a", ").中文）"],
    ["/a", ").𠮷野"],
    ["/a).txt", ""],
    ["/wiki/𠮷野", "）说明"],
    ["/Foo_(bar)", ""],
    ["/Foo_(bar)", "）继续"],
    ["/Foo_(bar.)", ").继续"],
    ["/Foo_(bar).中文", "」说明"],
    ["/a)b(c)中文", "」说明"],
    ["/wiki/人工智能", ""],
    ["/wiki/人工智能%EF%BC%89", "、扩展阅读"],
    ["", "）说明"],
  ])("recognizes %s with trailing %s", (path, tail) => {
    const label = origin + path;
    const nodes = paragraph(`${prefix}${label}${tail}`);
    const index = nodes.findIndex((node) => node.type === "link");
    expect(nodes[index]).toMatchObject({
      type: "link",
      url: scheme + label,
      children: [{ type: "text", value: label }],
    });
    expect(
      nodes
        .slice(index + 1)
        .map((node) => (node.type === "text" ? node.value : null))
        .join(""),
    ).toBe(tail);
  });
});

it("parses Markdown and additional links immediately after a CJK boundary", () => {
  expect(paragraph("（https://example.com/a）**说明**")).toMatchObject([
    { type: "text", value: "（" },
    { type: "link", url: "https://example.com/a" },
    { type: "text", value: "）" },
    { type: "strong", children: [{ type: "text", value: "说明" }] },
  ]);
  expect(paragraph("（www.example.com/a）www.example.org/path")).toMatchObject([
    { type: "text", value: "（" },
    { type: "link", url: "http://www.example.com/a" },
    { type: "text", value: "）" },
    { type: "link", url: "http://www.example.org/path" },
  ]);
});

it("leaves explicit links, code and existing start boundaries alone", () => {
  const url = "https://example.com/wiki/人工智能）说明";
  expect(paragraph(`[详细说明](${url}) <${url}> \`${url}\``)).toMatchObject([
    { type: "link", url, children: [{ type: "text", value: "详细说明" }] },
    { type: "text", value: " " },
    { type: "link", url, children: [{ type: "text", value: url }] },
    { type: "text", value: " " },
    { type: "inlineCode", value: url },
  ]);
  expect(parser.parse(`~~~text\n${url}\n~~~`).children).toMatchObject([
    { type: "code", value: url },
  ]);
  expect(paragraph("见www.example.com nothttps://example.com")).toMatchObject([
    { type: "text", value: "见www.example.com nothttps://example.com" },
  ]);
});
