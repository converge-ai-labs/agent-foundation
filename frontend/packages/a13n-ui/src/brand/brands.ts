import auth0Icon from "./auth0.svg";
import autheliaIcon from "./authelia.svg";
import authentikIcon from "./authentik.svg";
import boxdIcon from "./boxd.svg";
import cloudflareIcon from "./cloudflare.svg";
import githubIcon from "./github.svg";
import gitlabIcon from "./gitlab.svg";
import googleIcon from "./google.svg";
import keycloakIcon from "./keycloak.svg";
import { lobeBrands, lobeIconsCdn } from "./lobe-brands.generated";
import microsoftIcon from "./microsoft.svg";
import oktaIcon from "./okta.svg";
import typesafeIcon from "./typesafe.svg";
import zitadelDarkIcon from "./zitadel-dark.svg";
import zitadelIcon from "./zitadel.svg";

/** Display identities only. These mappings never select an endpoint or account. */
export interface Brand {
  icon: string;
  /** Both the base icon and any dark variant are bundled assets. */
  bundled?: boolean;
  darkIcon?: string;
  invertInDark?: boolean;
  aliases?: readonly string[];
  hosts?: readonly string[];
  endpoints?: readonly string[];
}

// Vite imports the URL directly; the Next.js image loader wraps it in metadata.
function assetUrl(asset: string | { src: string }): string {
  return typeof asset === "string" ? asset : asset.src;
}

const svgl = "https://svgl.app/library/";
const curatedBrands: Record<string, Brand> = {
  google: { icon: assetUrl(googleIcon), bundled: true },
  microsoft: { icon: assetUrl(microsoftIcon), bundled: true },
  okta: { icon: assetUrl(oktaIcon), bundled: true, invertInDark: true },
  auth0: { icon: assetUrl(auth0Icon), bundled: true, invertInDark: true },
  gitlab: { icon: assetUrl(gitlabIcon), bundled: true, invertInDark: true },
  authentik: {
    icon: assetUrl(authentikIcon),
    bundled: true,
    invertInDark: true,
  },
  keycloak: { icon: assetUrl(keycloakIcon), bundled: true, invertInDark: true },
  authelia: { icon: assetUrl(autheliaIcon), bundled: true, invertInDark: true },
  zitadel: {
    icon: assetUrl(zitadelIcon),
    darkIcon: assetUrl(zitadelDarkIcon),
    bundled: true,
  },
  github: {
    icon: assetUrl(githubIcon),
    bundled: true,
    invertInDark: true,
    hosts: ["github.com", "api.githubcopilot.com"],
  },
  notion: {
    icon: `${svgl}notion.svg`,
    invertInDark: true,
    hosts: ["mcp.notion.com", "notion.so", "notion.com"],
  },
  cloudflare: {
    ...lobeBrands.cloudflare,
    icon: assetUrl(cloudflareIcon),
    bundled: true,
    invertInDark: true,
    aliases: ["cloudflare_ai_gateway", "cloudflare-ai-gateway"],
    hosts: [
      "mcp.cloudflare.com",
      "cloudflare.com",
      "api.cloudflare.com",
      "gateway.ai.cloudflare.com",
    ],
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
    icon: `${lobeIconsCdn}vercel.svg`,
    invertInDark: true,
    aliases: ["vercel-ai-gateway", "vercel_ai_gateway"],
    hosts: ["mcp.vercel.com", "vercel.com", "ai-gateway.vercel.sh"],
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
  duckduckgo: { icon: "https://duckduckgo.com/favicon.ico" },
  parallel: { icon: "https://parallel.ai/favicon.ico" },
  tavily: { icon: `${lobeIconsCdn}tavily-color.svg` },
  firecrawl: { icon: `${lobeIconsCdn}firecrawl-color.svg` },
  jina: { icon: `${lobeIconsCdn}jina.svg` },
  perplexity: { icon: `${lobeIconsCdn}perplexity-color.svg` },
  serpapi: { icon: "https://serpapi.com/favicon.ico" },
  keenable: {
    icon: "https://framerusercontent.com/images/ltjNqddCBKH3F8nHENkzQKPac.png",
    hosts: ["keenable.ai", "app.keenable.ai"],
  },
  specstream: {
    icon: "https://specstream.ai/favicon.svg?v=mark-1",
    hosts: ["specstream.ai", "api.specstream.ai"],
  },
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
  fireworks: {
    icon: `${lobeIconsCdn}fireworks-color.svg`,
    aliases: ["fireworks-ai", "fireworks_ai", "fireworksai"],
    hosts: ["fireworks.ai", "app.fireworks.ai", "api.fireworks.ai"],
  },
  together: {
    icon: `${lobeIconsCdn}together-color.svg`,
    hosts: ["together.ai", "api.together.ai", "api.together.xyz"],
  },
  cerebras: {
    icon: `${lobeIconsCdn}cerebras-color.svg`,
    hosts: ["cerebras.ai", "cloud.cerebras.ai", "api.cerebras.ai"],
  },
  sambanova: {
    icon: `${lobeIconsCdn}sambanova-color.svg`,
    hosts: ["sambanova.ai", "cloud.sambanova.ai", "api.sambanova.ai"],
  },
  xai: {
    icon: `${lobeIconsCdn}xai.svg`,
    invertInDark: true,
    aliases: ["x-ai"],
    hosts: ["x.ai", "console.x.ai", "api.x.ai"],
  },
  ollama: { icon: `${lobeIconsCdn}ollama.svg`, invertInDark: true },
  alibaba_model_studio: { icon: `${lobeIconsCdn}qwen-color.svg` },
  deepseek: { icon: `${lobeIconsCdn}deepseek-color.svg` },
  // The color mark has a white letter; the monochrome mark works on both themes.
  kimi: { icon: `${lobeIconsCdn}kimi.svg`, invertInDark: true },
  moonshot: { icon: `${lobeIconsCdn}kimi.svg`, invertInDark: true },
  typesafe: {
    icon: assetUrl(typesafeIcon),
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
  browserbase: { icon: "https://www.browserbase.com/favicon.svg" },
  browser_use: {
    icon: "https://browser-use.com/logo-primary.svg",
    invertInDark: true,
  },
  kernel: { icon: "https://www.onkernel.com/favicon.svg" },
  kubernetes: {
    icon: "https://cdn.jsdelivr.net/gh/cncf/artwork@40e2e8948509b40e4bad479446aaec18d6273bf2/projects/kubernetes/icon/color/kubernetes-icon-color.svg",
  },
  boxd: { icon: boxdIcon, invertInDark: true },
  mem0: {
    icon: "https://framerusercontent.com/images/2ys67ADJdvcyGmQnhp8vKWSq8.svg",
    aliases: [
      "a13n.mem0-oss",
      "a13n.mem0-platform",
      "mem0-oss",
      "mem0-platform",
      "mem0_oss",
      "mem0_platform",
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
