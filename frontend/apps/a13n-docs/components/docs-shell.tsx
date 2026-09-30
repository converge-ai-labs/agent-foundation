import type { ReactNode } from "react";
import type { Locale } from "@/lib/i18n";
import type * as PageTree from "fumadocs-core/page-tree";
import { DocsLayout } from "fumadocs-ui/layouts/notebook";
import { DocsHeader } from "@/components/docs-header";
import { site } from "@/lib/site";
import { source } from "@/lib/source";

export function DocsShell({
  children,
  locale,
}: {
  children: ReactNode;
  locale: Locale;
}) {
  const { $ref: _, ...tree } = source.getPageTree(locale);
  return (
    <DocsLayout
      tree={{ ...tree, children: tree.children.map(navigationNode) }}
      tabMode="navbar"
      nav={{ mode: "top" }}
      githubUrl={site.repository}
      slots={{ header: DocsHeader }}
    >
      {children}
    </DocsLayout>
  );
}

// Every page serializes the navigation, so it drops what the layout never reads:
// page descriptions and source refs (these only map pages between versioned tabs).
function navigationNode(node: PageTree.Node): PageTree.Node {
  if (node.type === "page") {
    const { $ref: _, description: __, ...page } = node;
    return page;
  }
  if (node.type === "folder") {
    const { $ref: _, index, children, ...folder } = node;
    return {
      ...folder,
      index: index && (navigationNode(index) as PageTree.Item),
      children: children.map(navigationNode),
    };
  }
  return node;
}
