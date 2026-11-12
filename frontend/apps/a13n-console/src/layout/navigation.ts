import {
  type Icon,
  BrainIcon,
  GlobeIcon,
  LightbulbIcon,
  ChartBarIcon,
  PulseIcon,
  HeadCircuitIcon,
  CubeIcon,
  ChatsIcon,
  ClockIcon,
  MonitorIcon,
  PlugIcon,
  KeyIcon,
  PuzzlePieceIcon,
} from "@phosphor-icons/react";
export const navigationGroups: {
  label: string;
  entries: [string, string, Icon, [string, string][]?][];
}[] = [
  {
    label: "",
    entries: [
      ["agents", "Agents", HeadCircuitIcon],
      ["sessions", "Sessions", ChatsIcon],
      ["schedules", "Schedules", ClockIcon],
    ],
  },
  {
    label: "Capabilities",
    entries: [
      ["models", "Models", CubeIcon],
      ["skills", "Skills", PuzzlePieceIcon],
      [
        "memories",
        "Memories",
        BrainIcon,
        [
          ["memories", "Collections"],
          ["memory-spaces", "Spaces"],
        ],
      ],
      ["connections", "Connections", PlugIcon],
      ["credentials", "Credentials", KeyIcon],
      ["browsers", "Browsers", GlobeIcon],
      [
        "environments",
        "Environments",
        MonitorIcon,
        [
          ["environments", "Templates"],
          ["environments/instances", "Instances"],
          ["monitoring", "Metrics"],
        ],
      ],
    ],
  },
  {
    label: "Insights",
    entries: [
      ["usage", "Usage", ChartBarIcon],
      ["traces", "Traces", PulseIcon],
      ["findings", "Findings", LightbulbIcon],
    ],
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
    "/api-keys": 4,
    "/agents": 4,
    "/sessions": 4,
    "/schedules": 4,
    "/models": 4,
    "/skills": 3,
    "/memories": 5,
    "/memory-spaces": 5,
    "/environments": 4,
    "/browsers": 4,
    "/connections": 3,
    "/credentials": 5,
    "/traces": 5,
    "/findings": 5,
  };
  if (tail in lists) return { variant: "page", columns: lists[tail] };
  if (
    /^\/(agents|schedules|traces|environments|memories|memory-spaces|findings|credentials)\/[^/]+$/.test(
      tail,
    )
  )
    return { variant: "detail", columns: 4 };
  return undefined;
}
