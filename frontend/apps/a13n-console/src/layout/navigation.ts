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
  Settings2,
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
      ["models", "Models", Boxes],
      ["/providers", "Providers", Settings2],
      ["skills", "Skills", Sparkles],
      ["assets", "Assets", File],
      [
        "environments",
        "Environments",
        Monitor,
        [
          ["environments", "Templates"],
          ["environments/instances", "Instances"],
        ],
      ],
    ],
  },
  {
    label: "Integrations",
    entries: [
      ["application-accounts", "Application accounts", Cable],
      ["connectors", "Connectors", Plug],
      ["mcp", "MCP connections", Network],
    ],
  },
  {
    label: "Observe",
    entries: [["traces", "Traces", Activity]],
  },
];
