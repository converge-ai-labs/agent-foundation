// What the Service scene and the stack show. Brand names resolve through
// brandIcon, so each must be in the a13n-ui brand registry.

export interface AgentSpec {
  name: string;
  instructions: string;
  model: { brand: string; id: string };
  skills: string[];
  connections: string[];
  sandbox: string;
}

/** Act one: agents that the sheet describes in turn. */
export const AGENTS: AgentSpec[] = [
  {
    name: "PR reviewer",
    instructions:
      "Review every pull request. Flag real bugs, explain why, and suggest a fix.",
    model: { brand: "Claude", id: "claude-opus-5.5" },
    skills: ["code-review", "test-plan"],
    connections: ["GitHub", "Linear", "Sentry"],
    sandbox: "Docker",
  },
  {
    name: "Support triage",
    instructions:
      "Read each new ticket, find the likely cause, and route it to the right team.",
    model: { brand: "OpenAI", id: "gpt-6.1-sol" },
    skills: ["triage", "reply-drafts"],
    connections: ["Slack", "Atlassian", "Notion"],
    sandbox: "E2B",
  },
  {
    name: "Market brief",
    instructions:
      "Every morning, research what changed in our market and write a one-page brief.",
    model: { brand: "Gemini", id: "gemini-3.8-flash" },
    skills: ["web-research", "briefs"],
    connections: ["Notion", "Gmail", "Google Drive"],
    sandbox: "Modal",
  },
  {
    name: "Data on-call",
    instructions:
      "Keep the nightly pipeline green. When a job fails, find the cause and open a fix.",
    model: { brand: "Kimi", id: "kimi-k3" },
    skills: ["sql", "pipeline-debug"],
    connections: ["GitHub", "Slack", "Sentry"],
    sandbox: "Daytona",
  },
  {
    name: "Launch watch",
    instructions:
      "After each release, follow what people say about it. Group the complaints and file each new bug.",
    model: { brand: "Grok", id: "grok-4.7" },
    skills: ["sentiment", "bug-intake"],
    connections: ["Slack", "Linear", "HubSpot"],
    sandbox: "Vercel Sandbox",
  },
];

export type ThreadState = "accepted" | "running" | "completed" | "waiting";

export interface Call {
  agent: string;
  /** Two things the call asks for, in turn. */
  tasks: [string, string];
  /** The call's lines of code; ⟨⟩ marks what it asks for. */
  code: (task: string) => string[];
}

/** Act two: one call per SDK, in the order of the language rail, then HTTP. */
export const CALLS: Call[] = [
  {
    agent: "PR reviewer",
    tasks: ["Review PR #482", "Review PR #486"],
    code: (t) => [
      "agent = client.agents(PR_REVIEWER)",
      `await agent.start(⟨"${t}"⟩, idempotency_key=key)`,
    ],
  },
  {
    agent: "Support triage",
    tasks: ["Triage ticket 9132", "Triage ticket 9140"],
    code: (t) => [
      "const agent = client.agents.ref(SUPPORT_TRIAGE)",
      `await agent.start(⟨"${t}"⟩, { idempotencyKey })`,
    ],
  },
  {
    agent: "Market brief",
    tasks: ["Brief me on today", "Brief me on chips"],
    code: (t) => [
      "agent := client.Agent(marketBrief)",
      `agent.Start(ctx, ⟨"${t}"⟩,`,
      "\ta13n.StartOptions{RequestKey: key})",
    ],
  },
  {
    agent: "Data on-call",
    tasks: ["Why did the 02:00 job fail?", "Why is the 06:00 job slow?"],
    code: (t) => [
      "let agent = client.agent(DATA_ON_CALL);",
      `agent.start(⟨"${t}"⟩, key).await?;`,
    ],
  },
  {
    agent: "Launch watch",
    tasks: ["Watch the v2.4 launch", "Watch the v2.5 launch"],
    // the request body sits in a file named for the release
    code: (t) => [
      'curl -X POST "$A13N_URL/api/v1/threads" \\',
      '  -H "Idempotency-Key: $KEY" \\',
      `  -d @⟨watch-${t.split(" ")[2]}.json⟩`,
    ],
  },
];

/** Threads already on the board when it first comes into view. */
export const EARLIER: [task: string, agent: string, state: ThreadState][] = [
  ["Review PR #479", "PR reviewer", "completed"],
  ["Refund order 77120?", "Support triage", "waiting"],
  ["Draft the release notes", "Launch watch", "completed"],
  ["Summarize last night's alerts", "Data on-call", "running"],
];

/** How a running thread ends: mostly done, sometimes waiting for an answer. */
export const ENDS: ThreadState[] = [
  "completed",
  "completed",
  "completed",
  "waiting",
];

export interface StackRow {
  title: string;
  brands: string[];
  more?: string;
}

/** The providers an agent can use, by kind. */
export const STACK: StackRow[] = [
  {
    title: "Models",
    brands: [
      "OpenAI",
      "Anthropic",
      "Gemini",
      "Vertex AI",
      "Azure OpenAI",
      "Amazon Bedrock",
      "OpenRouter",
      "xAI",
      "DeepSeek",
      "Qwen",
      "Kimi",
      "MiniMax",
      "Zhipu",
      "Ollama",
      "Fireworks AI",
      "Together AI",
      "Cerebras",
      "SambaNova",
      "Vercel AI Gateway",
      "TypeSafe",
    ],
  },
  {
    title: "Sandboxes",
    brands: [
      "Docker",
      "E2B",
      "Daytona",
      "Modal",
      "Vercel Sandbox",
      "Fly.io Sprites",
      "Runloop",
    ],
  },
  {
    title: "Web data",
    brands: [
      "DuckDuckGo",
      "Brave",
      "Perplexity",
      "SerpApi",
      "Exa",
      "Parallel",
      "Tavily",
      "Firecrawl",
      "Jina",
      "TinyFish",
    ],
  },
  {
    title: "Connections",
    brands: [
      "GitHub",
      "Notion",
      "Figma",
      "Linear",
      "Slack",
      "Atlassian",
      "Stripe",
      "Sentry",
    ],
    more: "and any remote MCP server",
  },
];
