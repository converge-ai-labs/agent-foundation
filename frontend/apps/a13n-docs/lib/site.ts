import { basePath } from "./base-path.mjs";

export { basePath };

export const site = {
  name: "Agent Foundation",
  description:
    "The open-source, self-hosted foundation for AI agents: the Harness library, the managed Service, and the Harness UI workbench.",
  url: `https://a13n.converge.ai${basePath}`,
  repository: "https://github.com/converge-ai-labs/agent-foundation",
  branch: "main",
};

/** Repository files served next to the pages, by published name. */
export const referenceFiles = {
  "service-openapi.json": "proto/a13n-service/openapi.json",
  "harness-ui-openapi.json": "frontend/apps/a13n-harness-ui/src/openapi.json",
  "service-settings.json": "scripts/docs/service-settings.schema.json",
} as const;

export function sourceUrl(path: string) {
  return `${site.repository}/blob/${site.branch}/${path}`;
}

/** Published URL of a page's Markdown source, for readers and LLM tools. */
export function markdownSegments(slugs: string[]) {
  const segments = slugs.length === 0 ? ["index"] : [...slugs];
  segments.push(`${segments.pop()}.md`);
  return segments;
}

export function markdownUrl(slugs: string[]) {
  return `${basePath}/md/${markdownSegments(slugs).join("/")}`;
}

/** Markdown is read away from the site, so its site links carry the full URL. */
export function absoluteLinks(markdown: string) {
  return markdown.replaceAll("](/", `](${site.url}/`);
}
