import {
  type Icon,
  BrainIcon,
  MagnifyingGlassIcon,
  ChartBarIcon,
  PulseIcon,
  HeadCircuitIcon,
  CubeIcon,
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
      ["agents", "Agents", HeadCircuitIcon],
      ["sessions", "Sessions", ChatsIcon],
    ],
  },
  {
    label: "Resources",
    entries: [
      ["models", "Models", CubeIcon],
      ["skills", "Skills", PuzzlePieceIcon],
      ["memories", "Memories", BrainIcon],
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
    entries: [["connections", "Connections", PlugIcon]],
  },
  {
    label: "Observe",
    entries: [
      ["usage", "Usage", ChartBarIcon],
      ["traces", "Traces", PulseIcon],
    ],
  },
  {
    label: "Improve",
    entries: [["findings", "Findings", MagnifyingGlassIcon]],
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
    "/memories": 5,
    "/environments": 4,
    "/connections": 3,
    "/traces": 5,
    "/findings": 5,
  };
  if (tail in lists) return { variant: "page", columns: lists[tail] };
  if (/^\/(agents|traces|environments|memories|findings)\/[^/]+$/.test(tail))
    return { variant: "detail", columns: 4 };
  return undefined;
}
