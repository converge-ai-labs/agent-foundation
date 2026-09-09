import {
  type LucideIcon,
  Activity,
  Bot,
  Boxes,
  Cable,
  File,
  MessagesSquare,
  Monitor,
  Network,
  Plug,
  Sparkles,
  Search,
} from "lucide-react";
export const navigationGroups: {
  label: string;
  entries: [string, string, LucideIcon, [string, string][]?][];
}[] = [
  {
    label: "",
    entries: [
      ["agents", "Agents", Bot],
      ["sessions", "Sessions", MessagesSquare],
    ],
  },
  {
    label: "Resources",
    entries: [
      [
        "models",
        "Models",
        Boxes,
        [
          ["models", "All models"],
          ["models/providers", "Providers"],
        ],
      ],
      ["search-providers", "Search accounts", Search],
      ["skills", "Skills", Sparkles],
      ["assets", "Assets", File],
      [
        "environments",
        "Environments",
        Monitor,
        [
          ["environments", "Templates"],
          ["environments/instances", "Instances"],
          ["environments/providers", "Providers"],
        ],
      ],
    ],
  },
  {
    label: "Integrations",
    entries: [
      ["application-accounts", "Application accounts", Cable],
      [
        "connectors",
        "Connectors",
        Plug,
        [
          ["connectors", "Connections"],
          ["connectors/providers", "Providers"],
        ],
      ],
      ["mcp", "MCP connections", Network],
    ],
  },
  {
    label: "Observe",
    entries: [["traces", "Traces", Activity]],
  },
];
