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
  RobotIcon,
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
      ["bots", "Bots", RobotIcon],
      ["application-accounts", "Application accounts", PlugsConnectedIcon],
      ["connections", "Connections", PlugIcon],
    ],
  },
  {
    label: "Observe",
    entries: [["traces", "Traces", PulseIcon]],
  },
];

/**
 * Skeleton shape used while a route module loads. Only destinations whose
 * layout is predictable are listed; everything else falls back to a spinner.
 */
export function routeSkeleton(
  pathname: string,
): { variant: "page" | "detail"; columns: number } | undefined {
  const tail = pathname.replace(/^\/[^/]+\/[^/]+/, "");
  const lists: Record<string, number> = {
    "/agents": 4,
    "/sessions": 4,
    "/models": 4,
    "/skills": 3,
    "/memories": 4,
    "/environments": 4,
    "/bots": 4,
    "/application-accounts": 4,
    "/connections": 3,
    "/traces": 5,
  };
  if (tail in lists) return { variant: "page", columns: lists[tail] };
  if (/^\/(agents|bots|traces|environments)\/[^/]+$/.test(tail))
    return { variant: "detail", columns: 4 };
  return undefined;
}
