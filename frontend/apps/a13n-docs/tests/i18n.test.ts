import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fromMarkdown } from "mdast-util-from-markdown";
import { visit } from "unist-util-visit";
import { translateOpenAPI } from "../lib/translate-openapi.ts";
import { localeUrl } from "../lib/i18n.ts";
import {
  headingIds,
  remarkTranslationHeadings,
} from "../lib/remark-translation-headings.ts";

test("language links preserve pages and fragments but keep machine downloads English", () => {
  assert.equal(
    localeUrl("/a13n-service/agents-and-runs/#run", "zh-CN"),
    "/zh-CN/a13n-service/agents-and-runs/#run",
  );
  assert.equal(
    localeUrl("/zh-CN/a13n-service/?q=1#run", "en"),
    "/a13n-service/?q=1#run",
  );
  assert.equal(localeUrl("/zh-CN/", "zh-CN"), "/zh-CN/");
  assert.equal(localeUrl("/", "zh-CN"), "/zh-CN/");
  for (const url of [
    "/md/a13n-service/index.md",
    "/reference/service-openapi.json",
    "/llms.txt",
    "/llms-full.txt",
    "/LICENSE.coss",
    "https://github.com/a/b",
    "//example.com/",
  ])
    assert.equal(localeUrl(url, "zh-CN"), url);
});

test("canonical IDs handle front matter, formatting, duplicate headings and fenced examples", () => {
  assert.deepEqual(
    headingIds(
      "---\ntitle: Guide\n---\n\n## Use **tools**\n\n## Use **tools**\n\n```md\n## Hidden\n```\n\n### `AgentDefinition`\n\n## Custom [#stable]",
    ),
    [
      { depth: 2, id: "use-tools" },
      { depth: 2, id: "use-tools-1" },
      { depth: 3, id: "agentdefinition" },
      { depth: 2, id: "stable" },
    ],
  );
});

test("translated labels retain section IDs and incomplete structure fails compilation", async () => {
  const folder = await mkdtemp(join(tmpdir(), "a13n-docs-headings-"));
  try {
    const path = join(folder, "guide.zh-CN.md");
    await writeFile(
      join(folder, "guide.md"),
      "## Get started\n\n### Configure a model\n",
    );
    const root = fromMarkdown("## 开始使用\n\n### 配置模型\n");
    await remarkTranslationHeadings()(root, { path });
    const ids: unknown[] = [];
    visit(root, "heading", (heading) => {
      ids.push(heading.data?.hProperties?.id);
    });
    assert.deepEqual(ids, ["get-started", "configure-a-model"]);
    await assert.rejects(
      remarkTranslationHeadings()(fromMarkdown("## 开始使用\n"), { path }),
      /missing headings/,
    );
    await assert.rejects(
      remarkTranslationHeadings()(
        fromMarkdown("## 开始使用\n\n## 配置模型\n"),
        { path },
      ),
      /structure differs/,
    );
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});

test("API localization changes human prose while preserving identifiers and examples", () => {
  const document = {
    paths: {
      "/agents": {
        get: {
          summary: "Get agents",
          description: "Read agents",
          operationId: "get_agents",
          responses: { "200": { description: "Success" } },
        },
      },
    },
    components: {
      schemas: {
        Request: {
          description: "Request",
          properties: {
            description: { type: "string", description: "Human note" },
          },
          example: { description: "Human note" },
          default: { description: "Human note" },
        },
      },
    },
  };
  const translated = translateOpenAPI(document, {
    "Get agents": "获取 agent",
    "Read agents": "读取 agent",
    Success: "成功",
    Request: "请求",
    "Human note": "说明",
  });
  assert.equal(translated.paths["/agents"].get.summary, "获取 agent");
  assert.equal(translated.paths["/agents"].get.operationId, "get_agents");
  assert.equal(
    translated.components.schemas.Request.properties.description.description,
    "说明",
  );
  assert.deepEqual(translated.components.schemas.Request.example, {
    description: "Human note",
  });
  assert.deepEqual(translated.components.schemas.Request.default, {
    description: "Human note",
  });
  assert.equal(document.paths["/agents"].get.summary, "Get agents");
});

test("Chinese API prose covers every canonical summary and description", async () => {
  const document = JSON.parse(
    await readFile(
      new URL("../../../../proto/a13n-service/openapi.json", import.meta.url),
      "utf8",
    ),
  );
  const translations = JSON.parse(
    await readFile(
      new URL("../lib/locales/openapi.zh-CN.json", import.meta.url),
      "utf8",
    ),
  );
  const visit = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    for (const [key, value] of Object.entries(node)) {
      if (["example", "examples", "default", "const", "enum"].includes(key))
        continue;
      if (
        (key === "summary" || key === "description") &&
        typeof value === "string" &&
        value.trim()
      ) {
        assert.ok(translations[value], `Missing API translation: ${value}`);
        assert.match(translations[value], /[\u4e00-\u9fff]/, value);
      } else visit(value);
    }
  };
  visit(document);
});
