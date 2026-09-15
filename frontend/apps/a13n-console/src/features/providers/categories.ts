import {
  CubeIcon,
  DatabaseIcon,
  MagnifyingGlassIcon,
  MonitorIcon,
  PlugIcon,
} from "@phosphor-icons/react";

export const providerCategories = [
  {
    value: "models",
    label: "Model",
    icon: CubeIcon,
    description: "Connect model services and manage their credentials.",
  },
  {
    value: "search",
    label: "Search",
    icon: MagnifyingGlassIcon,
    description: "Connect search services for your agents' web tools.",
  },
  {
    value: "memory",
    label: "Memory",
    icon: DatabaseIcon,
    description: "Connect memory backends and manage their credentials.",
  },
  {
    value: "environments",
    label: "Environment",
    icon: MonitorIcon,
    description: "Configure where your agents' environments run.",
  },
  {
    value: "connectors",
    label: "Connector",
    icon: PlugIcon,
    description: "Connect integration services for external accounts.",
  },
] as const;

export function providerCategory(value: string | null) {
  return (
    providerCategories.find((item) => item.value === value) ??
    providerCategories[0]
  );
}
