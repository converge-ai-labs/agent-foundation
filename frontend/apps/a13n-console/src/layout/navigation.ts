import {
  type Icon,
  DatabaseIcon,
  PulseIcon,
  HeartIcon,
  CubeIcon,
  PlugsConnectedIcon,
  ChatsIcon,
  MonitorIcon,
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
      ["memories", "Memories", DatabaseIcon],
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
      ["connections", "Connections", PlugIcon],
    ],
  },
  {
    label: "Observe",
    entries: [["traces", "Traces", PulseIcon]],
  },
];
