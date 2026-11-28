import type { fromMarkdown } from "mdast-util-from-markdown";
import { citationTokens, type PreparedCitations } from "./citations";

type Root = ReturnType<typeof fromMarkdown>;
type RootContent = Root["children"][number];

/** Interpret private tokens only in Markdown prose, after code and HTML have been parsed. */
export function remarkCitations({ prefix, tokens }: PreparedCitations) {
  function restore(value: string) {
    return value.replace(
      citationTokens(prefix),
      (original, index: string | undefined) =>
        index === undefined
          ? original
          : (tokens[Number(index)]?.original ?? original),
    );
  }
  function walk(
    parent: Root | Extract<RootContent, { children: unknown }>,
    linked = false,
  ) {
    const children = parent.children as RootContent[];
    const result: RootContent[] = [];
    for (const node of children) {
      if (node.type === "text") {
        let offset = 0;
        for (const match of node.value.matchAll(citationTokens(prefix))) {
          const index = match.index;
          if (index > offset)
            result.push({
              type: "text",
              value: node.value.slice(offset, index),
            });
          const token =
            match[1] === undefined ? undefined : tokens[Number(match[1])];
          // Markdown links cannot contain another interactive element.
          if (linked) {
            result.push({
              type: "text",
              value: token?.original ?? "[source unavailable]",
            });
          } else {
            result.push({
              type: "text",
              value: "",
              data: {
                hName: "a13n-citation",
                hProperties: {
                  dataCitationNumbers: token?.numbers.join(",") ?? "",
                },
              },
            });
          }
          offset = index + match[0].length;
        }
        if (offset === 0) result.push(node);
        else if (offset < node.value.length)
          result.push({ type: "text", value: node.value.slice(offset) });
      } else {
        // A range can land in code or another Markdown literal. Restore its original bytes.
        if ("value" in node) node.value = restore(node.value);
        if ("url" in node) node.url = restore(node.url);
        if ("alt" in node && node.alt != null) node.alt = restore(node.alt);
        if ("title" in node && node.title != null)
          node.title = restore(node.title);
        if ("children" in node)
          walk(
            node,
            linked || node.type === "link" || node.type === "linkReference",
          );
        result.push(node);
      }
    }
    children.splice(0, children.length, ...result);
  }
  return (tree: Root) => walk(tree);
}
