import {
  CubeIcon,
  DatabaseIcon,
  GlobeIcon,
  MonitorIcon,
  PlugIcon,
} from "@phosphor-icons/react";

/**
 * One entry per provider category. The tables, empty states, and creation
 * dialogs read their nouns and copy from here so every category reads alike.
 */
export const providerCategories = [
  {
    value: "models",
    label: "Model",
    emptyTitle: "No model providers yet",
    icon: CubeIcon,
    description: "Connect model services and manage their credentials.",
    empty:
      "Add an account or endpoint, then choose the models your agents can use.",
  },
  {
    value: "web",
    label: "Web",
    emptyTitle: "No web providers yet",
    icon: GlobeIcon,
    description:
      "Connect search and scrape services for your agents' web tools.",
    empty: "Add a search or scrape service, then select it in your agent.",
  },
  {
    value: "memory",
    label: "Memory",
    emptyTitle: "No memory providers yet",
    icon: DatabaseIcon,
    description: "Connect memory backends and manage their credentials.",
    empty: "Add a memory backend, then select it in your agent.",
  },
  {
    value: "environments",
    label: "Environment",
    emptyTitle: "No environment providers yet",
    icon: MonitorIcon,
    description: "Configure where your agents' environments run.",
    empty:
      "Add a provider before creating templates or registering external environments.",
  },
  {
    value: "connectors",
    label: "Connector",
    emptyTitle: "No connector providers yet",
    icon: PlugIcon,
    description: "Connect integration services for external accounts.",
    empty: "Add an integration service, then connect accounts through it.",
  },
] as const;

export type ProviderCategoryValue =
  (typeof providerCategories)[number]["value"];

export function providerCategory(value: string | null) {
  return (
    providerCategories.find((item) => item.value === value) ??
    providerCategories[0]
  );
}
