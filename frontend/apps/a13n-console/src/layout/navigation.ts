import {
  type Icon,
  PulseIcon,
  HeartIcon,
  CubeIcon,
  PlugsConnectedIcon,
  FileIcon,
  ChatsIcon,
  MonitorIcon,
  TreeStructureIcon,
  PlugIcon,
  PuzzlePieceIcon,
} from "@phosphor-icons/react";
export const navigationGroups: {
  label: string;
  entries: [string, string, Icon, [string, string][]?][];
}[] = [
  {
    label: "",
    entries: [
      ["agents", "Agents", HeartIcon],
      ["sessions", "Sessions", ChatsIcon],
    ],
  },
  {
    label: "Resources",
    entries: [
      ["models", "Models", CubeIcon],
      ["skills", "Skills", PuzzlePieceIcon],
      ["assets", "Assets", FileIcon],
      [
        "environments",
        "Environments",
        MonitorIcon,
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
      ["application-accounts", "Application accounts", PlugsConnectedIcon],
      ["connectors", "Connectors", PlugIcon],
      ["mcp", "MCP connections", TreeStructureIcon],
    ],
  },
  {
    label: "Observe",
    entries: [["traces", "Traces", PulseIcon]],
  },
];
