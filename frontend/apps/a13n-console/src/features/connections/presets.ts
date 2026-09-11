import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPAuthMode"];
  docs: string;
  logo: string;
  requirements: string;
  oauthClient?: "preregistered";
}
/** Provider setup documentation and public discovery checked 2026-09-11.
 * Account authorization is still required; catalog inclusion is not an end-to-end account test.
 * A preset only prefills a new connection. Saved connections never follow catalog updates.
 */
export const mcpPresets: readonly MCPPreset[] = [
  {
    id: "asana",
    name: "Asana",
    description: "Work with tasks, projects, and portfolios.",
    endpoint: "https://mcp.asana.com/v2/mcp",
    logo: "https://svgl.app/library/asana-logo.svg",
    auth: "oauth",
    docs: "https://developers.asana.com/docs/integrating-with-asanas-mcp-server",
    requirements:
      "Create an Asana MCP app, register the displayed redirect URI, and distribute it to your workspace.",
    oauthClient: "preregistered",
  },
  {
    id: "aurora",
    name: "Aurora",
    description: "Search your Consilio matters, docs, and more.",
    endpoint: "https://mcp.ai.consilio.com",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/6a02b70a38d8e2b6e8fa1f71_consilio%20%281%29.svg",
    auth: "oauth",
    docs: "https://mcp-docs.ai.consilio.com/docs/quickstart/",
    requirements: "Requires a Consilio Aurora account.",
  },
  {
    id: "clerk",
    name: "Clerk",
    description: "Look up Clerk SDK documentation and snippets.",
    endpoint: "https://mcp.clerk.com/mcp",
    logo: "https://svgl.app/library/clerk-icon-light.svg",
    auth: "none",
    docs: "https://clerk.com/docs/guides/ai/mcp/clerk-mcp-server",
    requirements: "Public Clerk SDK documentation tools; no account required.",
  },
  {
    id: "clickup",
    name: "ClickUp",
    description: "Manage tasks, projects, and team collaboration.",
    endpoint: "https://mcp.clickup.com/mcp",
    logo: "https://svgl.app/library/clickup.svg",
    auth: "oauth",
    docs: "https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server",
    requirements: "Requires access to a ClickUp workspace.",
  },
  {
    id: "context7",
    name: "Context7",
    description:
      "Look up current library documentation with your Context7 API key.",
    endpoint: "https://mcp.context7.com/mcp",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/698690879ec0fe5a8ed0e319_context7-icon-green.png",
    auth: "bearer",
    docs: "https://context7.com/docs/resources/all-clients",
    requirements:
      "Create a Context7 API key. Anonymous access is also available with lower limits.",
  },
  {
    id: "deepwiki",
    name: "DeepWiki",
    description: "Explore documentation for public repositories.",
    endpoint: "https://mcp.deepwiki.com/mcp",
    logo: "",
    auth: "none",
    docs: "https://docs.devin.ai/work-with-devin/deepwiki-mcp",
    requirements: "Public repository documentation; no account required.",
  },
  {
    id: "dice",
    name: "Dice",
    description: "Search active technology jobs",
    endpoint: "https://mcp.dice.com/mcp",
    logo: "https://integrations.sh/logo/dice.com",
    auth: "none",
    docs: "https://www.dice.com/about/mcp",
    requirements: "Public job search; no account required.",
  },
  {
    id: "factset",
    name: "FactSet",
    description: "Financial data and analytics",
    endpoint: "https://mcp.factset.com/content/v1",
    logo: "https://developer.factset.com/favicon.ico",
    auth: "oauth",
    docs: "https://developer.factset.com/mcp/factset-ai-ready-data-mcp#connect-proprietary-mcp-client",
    requirements:
      "Requires a FactSet account with the relevant data entitlements.",
  },
  {
    id: "github",
    name: "GitHub",
    description:
      "Access repositories, issues, and pull requests with a personal access token.",
    endpoint: "https://api.githubcopilot.com/mcp/",
    logo: "https://svgl.app/library/github_light.svg",
    auth: "bearer",
    docs: "https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md",
    requirements:
      "Supply a GitHub personal access token with access to the intended repositories.",
  },
  {
    id: "hubspot",
    name: "HubSpot",
    description: "Work with CRM records and business data.",
    endpoint: "https://mcp.hubspot.com",
    logo: "https://cdn.simpleicons.org/hubspot",
    auth: "oauth",
    docs: "https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server",
    requirements:
      "Create a HubSpot MCP Auth App with the displayed redirect URI. Select client secret in request body.",
    oauthClient: "preregistered",
  },
  {
    id: "mailerlite",
    name: "MailerLite",
    description: "Manage email campaigns and subscribers.",
    endpoint: "https://mcp.mailerlite.com/mcp",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/69a666eeaf51ef9283ed2fd8_mailerlite.jpeg",
    auth: "oauth",
    docs: "https://developers.mailerlite.com/mcp",
    requirements: "Requires a MailerLite account.",
  },
  {
    id: "mdn",
    name: "MDN",
    description: "Web docs, search, and browser compatibility data",
    endpoint: "https://mcp.mdn.mozilla.net/",
    logo: "https://developer.mozilla.org/favicon.svg",
    auth: "none",
    docs: "https://developer.mozilla.org/en-US/mcp",
    requirements:
      "Public experimental MDN documentation tools; no account required.",
  },
  {
    id: "mem",
    name: "Mem",
    description: "Search and manage notes.",
    endpoint: "https://mcp.mem.ai/mcp",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/69a772eb46aaa3bf051c52ed_mem_logo_square%20%281%29.svg",
    auth: "oauth",
    docs: "https://docs.mem.ai/mcp/setup",
    requirements: "Requires a Mem workspace.",
  },
  {
    id: "midpage",
    name: "Midpage Legal Research",
    description: "Search legal sources and case law.",
    endpoint: "https://app.midpage.ai/mcp",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/699a550b5cce23be4022d0ab_midpage.svg",
    auth: "oauth",
    docs: "https://docs.midpage.ai/documentation/integration/mcp-tools",
    requirements: "Requires a Midpage account.",
  },
  {
    id: "morningstar",
    name: "Morningstar",
    description: "Investment research and data",
    endpoint: "https://mcp.morningstar.com/mcp",
    logo: "https://developer.morningstar.com/favicon-32x32.png",
    auth: "bearer",
    docs: "https://developer.morningstar.com/direct-web-services/documentation/morningstar-ai-integrations/morningstar-mcp-server/overview#programmatic-access",
    requirements:
      "Requires licensed Morningstar access. Obtain a bearer token through its Authentication API and replace it when it expires.",
  },
  {
    id: "motion",
    name: "Motion Creative Analytics",
    description: "Analyze creative advertising performance.",
    endpoint: "https://projects.motionapp.com/mcp",
    logo: "https://cdn.prod.website-files.com/61ba3b439a672312697272c7/62193dfca2d19738b5c6da9d_motion-favicon-256x256.png",
    auth: "oauth",
    docs: "https://help.motionapp.com/en/articles/14315735-motion-mcp",
    requirements:
      "Requires an eligible Motion Creative Analytics account and Owner, Admin, or Collaborator role. Performance data currently covers Meta.",
  },
  {
    id: "parallel",
    name: "Parallel Search",
    description: "Real-time web search and content extraction",
    endpoint: "https://search.parallel.ai/mcp",
    logo: "https://mcpservers.org/logos/parallel.svg",
    auth: "none",
    docs: "https://docs.parallel.ai/integrations/mcp/search-mcp",
    requirements:
      "Public web search with anonymous limits. A Parallel bearer API key enables higher limits.",
  },
  {
    id: "pendo",
    name: "Pendo (US)",
    description: "Product analytics, guides, feedback",
    endpoint: "https://app.pendo.io/mcp/v0/shttp",
    logo: "https://www.pendo.io/icon.svg",
    auth: "oauth",
    docs: "https://support.pendo.io/hc/en-us/articles/41102236924955-Connect-to-the-Pendo-MCP-server",
    requirements:
      "US subscription endpoint. An administrator must enable AI access; use your regional endpoint for other subscriptions.",
  },
  {
    id: "planetscale",
    name: "PlanetScale",
    description: "Manage Postgres and MySQL databases.",
    endpoint: "https://mcp.pscale.dev/mcp/planetscale",
    logo: "https://svgl.app/library/planetscale.svg",
    auth: "oauth",
    docs: "https://planetscale.com/docs/mcp-server",
    requirements: "Requires access to a PlanetScale organization.",
  },
  {
    id: "sentry",
    name: "Sentry",
    description: "Investigate application errors and performance.",
    endpoint: "https://mcp.sentry.dev/mcp",
    logo: "https://svgl.app/library/sentry.svg",
    auth: "oauth",
    docs: "https://mcp.sentry.dev/",
    requirements: "Requires access to a Sentry organization.",
  },
  {
    id: "unthread",
    name: "Unthread",
    description: "Manage customer support tickets.",
    endpoint: "https://app.unthread.io/api/mcp",
    logo: "https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/69f527771c5044192f705995_unthread-icon.svg",
    auth: "oauth",
    docs: "https://docs.unthread.io/docs/unthread-ai/unthread-mcp",
    requirements: "Requires an active Unthread account and workspace access.",
  },
  {
    id: "zoom",
    name: "Zoom",
    description: "Meetings, recordings, summaries",
    endpoint: "https://mcp.zoom.us/mcp/zoom/streamable",
    logo: "https://cdn.simpleicons.org/zoom",
    auth: "oauth",
    docs: "https://developers.zoom.us/docs/mcp/servers/connect-to-zoom-mcp-servers/",
    requirements:
      "Create a Zoom General app with the displayed redirect URI, product licenses, and tool scopes. Select client secret in Basic header; administrator or developer access is required.",
    oauthClient: "preregistered",
  },
];
