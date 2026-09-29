export const site = {
  name: "Agent Foundation",
  description:
    "The open-source, self-hosted foundation for AI agents: the Harness library, the managed Service, and the Harness UI workbench.",
  url: "https://a13n-docs.converge.ai",
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
  return `/md/${markdownSegments(slugs).join("/")}`;
}
