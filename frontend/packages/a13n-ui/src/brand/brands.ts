import { lobeBrands, lobeIconsCdn } from "./lobe-brands.generated";
import typesafeIcon from "./typesafe.svg";

/** Display identities only. These mappings never select an endpoint or account. */
export interface Brand {
  icon: string;
  darkIcon?: string;
  invertInDark?: boolean;
  aliases?: readonly string[];
  hosts?: readonly string[];
  endpoints?: readonly string[];
}

const svgl = "https://svgl.app/library/";
const curatedBrands: Record<string, Brand> = {
  github: {
    icon: `${svgl}github_light.svg`,
    darkIcon: `${svgl}github_dark.svg`,
    hosts: ["github.com", "api.githubcopilot.com"],
  },
  notion: {
    icon: `${svgl}notion.svg`,
    invertInDark: true,
    hosts: ["mcp.notion.com", "notion.so", "notion.com"],
  },
  cloudflare: {
    ...lobeBrands.cloudflare,
    hosts: ["mcp.cloudflare.com", "cloudflare.com"],
  },
  linear: {
    icon: `${svgl}linear.svg`,
    hosts: ["mcp.linear.app", "linear.app"],
  },
  slack: { icon: `${svgl}slack.svg`, hosts: ["mcp.slack.com", "slack.com"] },
  gmail: { icon: `${svgl}gmail.svg`, hosts: ["mail.google.com"] },
  googlecalendar: {
    icon: `${svgl}google-calendar.svg`,
    aliases: ["google_calendar", "google-calendar"],
    hosts: ["calendar.google.com"],
  },
  googlesheets: {
    icon: `${svgl}google-sheets.svg`,
    aliases: ["google_sheets", "google-sheets"],
  },
  googledrive: {
    icon: "https://cdn.simpleicons.org/googledrive",
    aliases: ["google_drive", "google-drive"],
    hosts: ["drive.google.com"],
  },
  asana: {
    icon: `${svgl}asana-logo.svg`,
    hosts: ["mcp.asana.com", "app.asana.com"],
  },
  atlassian: {
    icon: `${svgl}atlassian.svg`,
    aliases: ["jira", "confluence"],
    hosts: ["mcp.atlassian.com"],
  },
  stripe: {
    icon: `${svgl}stripe.svg`,
    hosts: ["mcp.stripe.com", "stripe.com"],
  },
  sentry: {
    icon: `${svgl}sentry.svg`,
    invertInDark: true,
    hosts: ["mcp.sentry.dev", "sentry.io"],
  },
  vercel: {
    icon: `${svgl}vercel.svg`,
    darkIcon: `${svgl}vercel_dark.svg`,
    hosts: ["mcp.vercel.com", "vercel.com"],
  },
  hubspot: {
    icon: "https://cdn.simpleicons.org/hubspot",
    hosts: ["mcp.hubspot.com"],
  },
  dropbox: {
    icon: "https://cdn.simpleicons.org/dropbox",
    hosts: ["mcp.dropbox.com", "dropbox.com"],
  },
  context7: {
    icon: "https://cdn.jsdelivr.net/gh/upstash/context7@master/public/context7-icon-green.svg",
    hosts: ["mcp.context7.com", "context7.com"],
  },
  deepwiki: {
    icon: "https://deepwiki.com/icon.png",
    hosts: ["mcp.deepwiki.com", "deepwiki.com"],
  },
  composio: {
    icon: "https://composio.dev/logos/composio-black.svg",
    invertInDark: true,
  },
  brave: { icon: `${lobeIconsCdn}brave-color.svg` },
  exa: { icon: `${lobeIconsCdn}exa-color.svg` },
  duckduckgo: { icon: "https://cdn.simpleicons.org/duckduckgo" },
  parallel: { icon: "https://parallel.ai/favicon.ico" },
  tavily: { icon: `${lobeIconsCdn}tavily-color.svg` },
  firecrawl: { icon: `${lobeIconsCdn}firecrawl-color.svg` },
  jina: { icon: `${lobeIconsCdn}jina.svg` },
  perplexity: { icon: `${lobeIconsCdn}perplexity-color.svg` },
  serpapi: { icon: "https://serpapi.com/favicon.ico" },
  tinyfish: {
    icon: "https://www.tinyfish.ai/favicon-for-app/icon0.svg",
    hosts: ["tinyfish.ai", "www.tinyfish.ai", "agent.tinyfish.ai"],
  },
  openai: { icon: `${lobeIconsCdn}openai.svg`, invertInDark: true },
  anthropic: { icon: `${lobeIconsCdn}anthropic.svg`, invertInDark: true },
  google_gemini: { icon: `${lobeIconsCdn}gemini-color.svg` },
  google_vertex: { icon: `${lobeIconsCdn}vertexai-color.svg` },
  azure_openai: { icon: `${lobeIconsCdn}azure-color.svg` },
  aws_bedrock: { icon: `${lobeIconsCdn}bedrock-color.svg` },
  openrouter: { icon: `${lobeIconsCdn}openrouter-color.svg` },
  ollama: { icon: `${lobeIconsCdn}ollama.svg`, invertInDark: true },
  alibaba_model_studio: { icon: `${lobeIconsCdn}qwen-color.svg` },
  deepseek: { icon: `${lobeIconsCdn}deepseek-color.svg` },
  // The color mark has a white letter; the monochrome mark works on both themes.
  kimi: { icon: `${lobeIconsCdn}kimi.svg`, invertInDark: true },
  moonshot: { icon: `${lobeIconsCdn}kimi.svg`, invertInDark: true },
  typesafe: {
    icon: typesafeIcon,
    invertInDark: true,
    hosts: ["typesafe.ai", "api.typesafe.ai"],
  },
  zhipu: { icon: `${lobeIconsCdn}zhipu-color.svg` },
  docker: {
    icon: "https://cdn.jsdelivr.net/gh/pheralb/svgl@41d98985b481036562a6406e5c361af8a2781493/static/library/docker.svg",
  },
  e2b: { icon: "https://e2b.dev/brand/e2b-symbol-fire-orange-s.svg" },
  // Marks published by the vendors themselves; the registry links, never copies.
  modal: { icon: "https://modal.com/assets/favicon.svg", hosts: ["modal.com"] },
  daytona: {
    icon: "https://framerusercontent.com/images/6WPclDLAHHQgPFeA2DRTW1OXVSU.png",
    darkIcon:
      "https://framerusercontent.com/images/cCvSUNbGejoZpWVg0tUGLSqxGC8.png",
    hosts: ["daytona.io", "app.daytona.io"],
  },
  runloop: {
    icon: "https://docs.runloop.ai/favicon.svg",
    invertInDark: true,
    hosts: ["runloop.ai", "platform.runloop.ai"],
  },
  flyio: {
    icon: "https://fly.io/static/images/brand/brandmark.svg",
    aliases: ["fly", "fly.io", "sprites"],
    hosts: ["fly.io"],
  },
  mem0: {
    icon: "https://framerusercontent.com/images/2ys67ADJdvcyGmQnhp8vKWSq8.svg",
    aliases: [
      "a13n.mem0-oss",
      "a13n.mem0-platform",
      "mem0-oss",
      "mem0-platform",
    ],
    hosts: ["mem0.ai", "app.mem0.ai", "api.mem0.ai"],
  },
};

export const brands = mergeBrandCatalogs(lobeBrands, curatedBrands);

const brandsByEndpoint = new Map<string, Brand>();
for (const brand of Object.values(brands))
  for (const endpoint of brand.endpoints ?? [])
    brandsByEndpoint.set(canonicalEndpoint(endpoint), brand);

export function resolveBrand({
  identity,
  alias,
  endpoint,
}: {
  identity?: string;
  alias?: string;
  endpoint?: string;
}): Brand | undefined {
  const normalizedIdentity = identity?.trim().toLowerCase();
  if (normalizedIdentity && brands[normalizedIdentity])
    return brands[normalizedIdentity];
  const normalized = alias?.trim().toLowerCase();
  if (normalized) {
    if (brands[normalized]) return brands[normalized];
    const matched = Object.values(brands).find((brand) =>
      brand.aliases?.includes(normalized),
    );
    if (matched) return matched;
  }
  if (endpoint) {
    try {
      const url = new URL(endpoint);
      const exact = brandsByEndpoint.get(url.href);
      if (exact) return exact;
      const hostname = url.hostname.toLowerCase();
      return Object.values(brands).find((brand) =>
        brand.hosts?.includes(hostname),
      );
    } catch {
      /* A custom URL can be incomplete while it is being edited. */
    }
  }
  return undefined;
}

function mergeBrandCatalogs(
  ...catalogs: readonly Readonly<Record<string, Brand>>[]
): Record<string, Brand> {
  const merged: Record<string, Brand> = {};
  for (const catalog of catalogs)
    for (const [identity, brand] of Object.entries(catalog)) {
      const previous = merged[identity];
      merged[identity] = {
        ...previous,
        ...brand,
        aliases: mergeValues(previous?.aliases, brand.aliases),
        hosts: mergeValues(previous?.hosts, brand.hosts),
        endpoints: mergeValues(previous?.endpoints, brand.endpoints),
      };
    }
  return merged;
}

function mergeValues(
  previous?: readonly string[],
  current?: readonly string[],
): readonly string[] | undefined {
  const values = [...(previous ?? []), ...(current ?? [])];
  return values.length ? [...new Set(values)] : undefined;
}

function canonicalEndpoint(endpoint: string): string {
  return new URL(endpoint).href;
}
