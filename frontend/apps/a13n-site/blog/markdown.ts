import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { codeThemes, languageTitles } from "a13n-ui/code-theme";
import GithubSlugger from "github-slugger";
import type { Element, ElementContent, Parents, Root } from "hast";
import { toHtml } from "hast-util-to-html";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { bundledLanguages, codeToHast } from "shiki";
import { unified } from "unified";
import { visit } from "unist-util-visit";

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype, {
    footnoteLabel: "Notes",
    footnoteLabelProperties: { className: ["notes-head"] },
  });

/** A post body as HTML, with its word count for the reading time. */
export async function render(markdown: string, dir: string) {
  const mdast = processor.parse(markdown);
  let words = 0;
  visit(mdast, "text", (node) => {
    words += node.value
      .split(/\s+/)
      .filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
  });
  const hast = (await processor.run(mdast)) as Root;
  linkHeadings(hast);
  await Promise.all([highlightCode(hast), placeFigures(hast, dir)]);
  return { html: toHtml(hast, { allowDangerousHtml: true }), words };
}

const el = (
  tagName: string,
  properties: Element["properties"],
  children: ElementContent[] = [],
): Element => ({ type: "element", tagName, properties, children });

const text = (node: ElementContent | Root): string =>
  node.type === "text"
    ? node.value
    : "children" in node
      ? node.children.map((child) => text(child as ElementContent)).join("")
      : "";

/** Section headings get GitHub's IDs and a link to themselves. */
function linkHeadings(tree: Root) {
  const slugger = new GithubSlugger();
  visit(tree, "element", (node) => {
    if (!/^h[23]$/.test(node.tagName) || node.properties.id) return;
    node.properties.id = slugger.slug(text(node));
    node.children.push(
      el(
        "a",
        {
          className: ["anchor"],
          href: `#${node.properties.id}`,
          ariaLabel: "Section link",
        },
        [{ type: "text", value: "#" }],
      ),
    );
  });
}

/** Fenced code in the shared code palette, under a bar with its file name or language and a copy button. */
async function highlightCode(tree: Root) {
  const blocks: { pre: Element; parent: Parents }[] = [];
  visit(tree, "element", (node, _index, parent) => {
    if (node.tagName === "pre" && parent) blocks.push({ pre: node, parent });
  });
  await Promise.all(
    blocks.map(async ({ pre, parent }) => {
      const code = pre.children[0] as Element;
      const lang = String(
        (code.properties.className as string[] | undefined)?.[0] ?? "",
      ).replace(/^language-/, "");
      const meta = String(code.data?.meta ?? "");
      const label = /title="([^"]+)"/.exec(meta)?.[1] ?? languageTitles[lang];
      const highlighted = await codeToHast(text(code).replace(/\n$/, ""), {
        lang: lang in bundledLanguages ? lang : "text",
        theme: codeThemes.light,
      });
      const shiki = highlighted.children[0] as Element;
      // the page owns the background; the palette owns the text color
      shiki.properties.style = String(shiki.properties.style ?? "").replace(
        /background-color:[^;]+;?/,
        "",
      );
      const block = el("div", { className: ["codeblock"] }, [
        el("div", { className: ["cb-head"] }, [
          el("span", {}, label ? [{ type: "text", value: label }] : []),
          el("button", { className: ["cb-copy"], type: "button" }, [
            { type: "text", value: "Copy" },
          ]),
        ]),
        shiki,
      ]);
      parent.children.splice(parent.children.indexOf(pre), 1, block);
    }),
  );
}

/**
 * A paragraph holding only an image becomes a figure captioned by the image's
 * title. SVG drawings are inlined so they take the page's faces and line colors.
 */
async function placeFigures(tree: Root, dir: string) {
  const figures: { paragraph: Element; image: Element; parent: Parents }[] = [];
  visit(tree, "element", (node, _index, parent) => {
    // post images are served beside the page, not bundled
    if (node.tagName === "img") node.properties["vite-ignore"] = "";
    if (node.tagName !== "p" || !parent) return;
    const content = node.children.filter(
      (child) => child.type !== "text" || child.value.trim(),
    );
    if (
      content.length === 1 &&
      content[0].type === "element" &&
      content[0].tagName === "img"
    )
      figures.push({ paragraph: node, image: content[0], parent });
  });
  await Promise.all(
    figures.map(async ({ paragraph, image, parent }) => {
      const src = String(image.properties.src);
      const caption = image.properties.title;
      delete image.properties.title;
      let body: ElementContent = image;
      if (!/^[a-z]+:/i.test(src) && src.endsWith(".svg")) {
        const svg = await readFile(join(dir, src), "utf8");
        // narrow screens keep the drawing at its drawn width, so labels stay legible
        const width = /viewBox="[\d.-]+ [\d.-]+ ([\d.]+)/.exec(svg)?.[1];
        body = el(
          "div",
          {
            className: ["fig-scroll"],
            role: "img",
            ariaLabel: String(image.properties.alt ?? ""),
            style: width ? `--w: ${width}px` : undefined,
          },
          [{ type: "raw", value: svg.replace(/^<\?xml[^>]*>\s*/, "") }],
        );
      }
      const children: ElementContent[] = [body];
      if (caption) {
        // a leading "Figure 1." is the caption's label
        const [, label, rest] = /^(Figure \d+\.)?\s*(.*)$/s.exec(
          String(caption),
        )!;
        children.push(
          el("figcaption", {}, [
            ...(label ? [el("b", {}, [{ type: "text", value: label }])] : []),
            { type: "text", value: label ? ` ${rest}` : rest },
          ]),
        );
      }
      parent.children.splice(
        parent.children.indexOf(paragraph),
        1,
        el("figure", { className: ["fig"] }, children),
      );
    }),
  );
}
