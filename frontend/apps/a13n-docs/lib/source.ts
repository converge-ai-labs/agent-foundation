import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { remarkMdxMermaid } from "fumadocs-core/mdx-plugins";
import { llms, loader, type InferPageType } from "fumadocs-core/source";
import { metaSchema, pageSchema } from "fumadocs-core/source/schema";
import { applyMdxPreset } from "fumadocs-mdx/config";
import { defineDocs } from "fumadocs-mdx/macro";
import { createOpenAPI } from "fumadocs-openapi/server";
import { createElement, Fragment } from "react";
import { z } from "zod";
import { icon } from "./icons";
import { remarkAlerts } from "./remark-alerts";
import { referenceFiles } from "./site";

const docs = defineDocs({
  dir: "../../../docs",
  docs: {
    // `sidebarTitle` shortens a navigation label without changing the page title.
    schema: pageSchema.extend({ sidebarTitle: z.string().optional() }),
    postprocess: { includeProcessedMarkdown: true },
    mdxOptions: applyMdxPreset({
      remarkPlugins: [remarkAlerts, remarkMdxMermaid],
      rehypeCodeOptions: {
        themes: { light: "github-light", dark: "github-dark" },
      },
    }),
  },
  meta: { schema: metaSchema },
});

// The exported document names no server; examples target the local quickstart deployment.
const serviceDocument = {
  ...JSON.parse(
    await readFile(
      join(process.cwd(), "../../..", referenceFiles["service-openapi.json"]),
      "utf8",
    ),
  ),
  servers: [
    {
      url: "http://127.0.0.1:8080",
      description: "Local quickstart deployment",
    },
  ],
};

export const openapi = createOpenAPI({ input: { service: serviceDocument } });

export const source = loader({
  baseUrl: "/",
  source: {
    docs: docs.toFumadocsSource(),
    openapi: await openapi.staticSource({
      baseDir: "a13n-service/api-reference",
      per: "operation",
      // Untagged operations are the process probes (`/healthz`, `/readyz`).
      groupBy: (entry) =>
        entry.type === "operation"
          ? (serviceDocument.paths[entry.item.path]?.[entry.item.method]
              ?.tags?.[0] ?? "health")
          : "webhooks",
      // Readable URLs from operation summaries, such as `runs/create-run`.
      name: (entry) =>
        entry.info.title
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, ""),
      meta: true,
    }),
  },
  icon,
  plugins: ({ typedPlugin }) => [
    typedPlugin({
      name: "navigation-labels",
      transformPageTree: {
        // API reference folders are named by lowercase OpenAPI tags.
        folder(node, folderPath) {
          if (
            folderPath.startsWith("a13n-service/api-reference/") &&
            typeof node.name === "string"
          ) {
            node.name = node.name.charAt(0).toUpperCase() + node.name.slice(1);
          }
          return node;
        },
        file(node, filePath) {
          const file = filePath ? this.storage.read(filePath) : undefined;
          if (file?.format !== "page") return node;
          if (file.type === "docs" && file.data.sidebarTitle) {
            node.name = file.data.sidebarTitle;
          }
          // Every page serializes the navigation, so operations get a bare
          // method badge that `global.css` styles.
          const method =
            file.type === "openapi" ? file.data._openapi.method : undefined;
          if (method) {
            node.name = createElement(
              Fragment,
              null,
              node.name,
              " ",
              createElement(
                "span",
                { "data-method": method },
                method.toUpperCase(),
              ),
            );
          }
          return node;
        },
      },
    }),
  ],
});

export type DocsPage = InferPageType<typeof source>;

export const docsLlms = llms(source, {
  async renderPage(page) {
    if (page.type !== "docs") return "";
    return `# ${page.data.title}\n\n${await page.data.getText("processed")}`;
  },
});
