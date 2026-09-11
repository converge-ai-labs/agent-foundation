import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPAuthMode"];
  docs: string;
  unavailableReason?: string;
}
/** Curated from mcpservers.org/remote-mcp-servers; official sources checked 2026-09-11.
 * A preset only prefills a new connection. Saved endpoints and credentials never follow catalog updates.
 */
export const mcpPresets: readonly MCPPreset[] = [
  {
    id: "notion",
    name: "Notion",
    description: "Search and work with pages and databases.",
    endpoint: "https://mcp.notion.com/mcp",
    auth: "oauth",
    docs: "https://developers.notion.com/guides/mcp/get-started-with-mcp",
  },
  {
    id: "linear",
    name: "Linear",
    description: "Work with issues, projects, and teams.",
    endpoint: "https://mcp.linear.app/mcp",
    auth: "oauth",
    docs: "https://linear.app/docs/mcp",
  },
  {
    id: "github",
    name: "GitHub",
    description:
      "Access repositories, issues, and pull requests with a personal access token.",
    endpoint: "https://api.githubcopilot.com/mcp/",
    auth: "bearer",
    docs: "https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md",
  },
  {
    id: "stripe",
    name: "Stripe",
    description: "Work with your Stripe account and payments.",
    endpoint: "https://mcp.stripe.com",
    auth: "oauth",
    docs: "https://docs.stripe.com/mcp",
  },
  {
    id: "sentry",
    name: "Sentry",
    description: "Investigate application errors and performance.",
    endpoint: "https://mcp.sentry.dev/mcp",
    auth: "oauth",
    docs: "https://mcp.sentry.dev/",
  },
  {
    id: "vercel",
    name: "Vercel",
    description: "Inspect projects and deployments.",
    endpoint: "https://mcp.vercel.com",
    auth: "oauth",
    docs: "https://vercel.com/docs/agent-resources/vercel-mcp",
    unavailableReason:
      "Vercel's OAuth resource address requires a trailing slash that this client's endpoint normalization does not preserve.",
  },
  {
    id: "atlassian",
    name: "Atlassian",
    description: "Work with Jira and Confluence using an Atlassian API token.",
    endpoint: "https://mcp.atlassian.com/v2/mcp",
    auth: "bearer",
    docs: "https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/",
  },
  {
    id: "context7",
    name: "Context7",
    description:
      "Look up current library documentation with your Context7 API key.",
    endpoint: "https://mcp.context7.com/mcp",
    auth: "bearer",
    docs: "https://context7.com/docs/resources/all-clients",
  },
  {
    id: "deepwiki",
    name: "DeepWiki",
    description: "Explore documentation for public repositories.",
    endpoint: "https://mcp.deepwiki.com/mcp",
    auth: "none",
    docs: "https://docs.devin.ai/work-with-devin/deepwiki-mcp",
  },
  {
    id: "asana",
    name: "Asana",
    description: "Work with tasks, projects, and portfolios.",
    endpoint: "https://mcp.asana.com/v2/mcp",
    auth: "oauth",
    docs: "https://developers.asana.com/docs/connecting-mcp-clients-to-asanas-v2-server",
    unavailableReason:
      "Asana requires a pre-registered OAuth app. This client supports dynamic registration and client metadata documents.",
  },
];
