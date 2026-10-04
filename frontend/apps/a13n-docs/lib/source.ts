import { readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  rehypeCodeDefaultOptions,
  remarkMdxMermaid,
} from "fumadocs-core/mdx-plugins";
import { llms, loader, type InferPageType } from "fumadocs-core/source";
import { metaSchema, pageSchema } from "fumadocs-core/source/schema";
import { applyMdxPreset } from "fumadocs-mdx/config";
import { defineDocs } from "fumadocs-mdx/macro";
import { createOpenAPI } from "fumadocs-openapi/server";
import { createElement, Fragment } from "react";
import { z } from "zod";
import { codeThemes, transformerLanguageTitle } from "./code-blocks";
import { icon } from "./icons";
import { i18n } from "./i18n";
import openapiChinese from "./locales/openapi.zh-CN.json";
import { translateOpenAPI } from "./translate-openapi";
import { remarkTranslationHeadings } from "./remark-translation-headings";
import { remarkAlerts } from "./remark-alerts";
import { absoluteLinks, referenceFiles } from "./site";

const docs = defineDocs({
  dir: "../../../docs",
  docs: {
    // `sidebarTitle` shortens a navigation label without changing the page title.
    schema: pageSchema.extend({ sidebarTitle: z.string().optional() }),
    postprocess: { includeProcessedMarkdown: true },
    mdxOptions: applyMdxPreset({
      remarkPlugins: (plugins) => [
        remarkTranslationHeadings,
        ...plugins,
        remarkAlerts,
        remarkMdxMermaid,
      ],
      rehypeCodeOptions: {
        themes: codeThemes,
        transformers: [
          ...(rehypeCodeDefaultOptions.transformers ?? []),
          transformerLanguageTitle,
        ],
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

const apiSource = await openapi.staticSource({
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
});
const apiTranslations: Record<string, string> = {
  ...openapiChinese,
  "Local quickstart deployment": "本地快速入门部署",
};
const localizedDocument = translateOpenAPI(serviceDocument, apiTranslations);
const apiFolderNames: Record<string, string> = {
  agents: "Agent",
  assets: "资源包",
  sessions: "会话",
  threads: "线程",
  runs: "执行",
  items: "条目",
  models: "模型",
  workspaces: "工作空间",
  organizations: "组织",
  connections: "连接",
  skills: "Skill",
  environments: "环境",
  memories: "记忆",
  files: "文件",
  health: "健康检查",
  uploads: "上传",
  users: "用户",
  auth: "认证",
  identity: "身份",
  webhooks: "Webhook",
  providers: "Provider",
  subscriptions: "订阅",
  tenancy: "租户管理",
  usage: "用量",
};

const chineseApiFiles: typeof apiSource.files = apiSource.files.map((file) => {
  const path = file.path.replace(/(\.[^/.]+)$/, ".zh-CN$1");
  if (file.type === "meta")
    return {
      ...file,
      path,
      data: {
        ...file.data,
        title: file.data.title
          ? (apiFolderNames[file.data.title] ?? file.data.title)
          : undefined,
      },
    };
  return {
    ...file,
    path,
    data: {
      ...file.data,
      title: file.data.title
        ? (apiTranslations[file.data.title] ?? file.data.title)
        : undefined,
      description: file.data.description
        ? (apiTranslations[file.data.description] ?? file.data.description)
        : undefined,
      structuredData: {
        headings: file.data.structuredData.headings.map((heading) => ({
          ...heading,
          content: apiTranslations[heading.content] ?? heading.content,
        })),
        contents: file.data.structuredData.contents.map((content) => ({
          ...content,
          content: apiTranslations[content.content] ?? content.content,
        })),
      },
      getOpenAPIPageProps() {
        const props = file.data.getOpenAPIPageProps();
        return {
          ...props,
          payload: { ...props.payload, bundled: localizedDocument },
        };
      },
    },
  };
});

const localizedApiSource: typeof apiSource = {
  files: [...apiSource.files, ...chineseApiFiles],
};

export const source = loader({
  baseUrl: "/",
  i18n,
  source: {
    docs: docs.toFumadocsSource(),
    openapi: localizedApiSource,
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
    return absoluteLinks(
      `# ${page.data.title}\n\n${await page.data.getText("processed")}`,
    );
  },
});
