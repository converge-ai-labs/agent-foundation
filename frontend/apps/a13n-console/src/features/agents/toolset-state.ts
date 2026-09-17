import type { Schema } from "../../shared/api";

type Definition = Schema["ToolsetDefinition"];
type Tool = Definition["tools"][number];
type Selection = Schema["ToolSelection"] | undefined;
export type WebOperation = "search" | "scrape";
export type WebProvider = Schema["WebProvider"];
export type WebProviderDefinition = Schema["WebProviderDefinition"];

export function eligibleWebProvider(
  provider: WebProvider,
  definitions: WebProviderDefinition[],
  operation: WebOperation,
) {
  return (
    provider.enabled &&
    definitions.some(
      (definition) =>
        definition.type === provider.type &&
        (!definition.credential_required || provider.credential_configured) &&
        definition.operations.includes(operation),
    )
  );
}

export function toolState(
  groupEnabled: boolean,
  tool: Tool,
  selection: Selection,
  providers: WebProvider[],
  definitions: WebProviderDefinition[],
) {
  const currentId = selection?.config?.provider_id;
  const selector = tool.resource_selector;
  const eligible = selector
    ? providers.filter((provider) =>
        eligibleWebProvider(provider, definitions, selector.operation),
      )
    : [];
  const provider =
    typeof currentId === "string" && currentId
      ? eligible.find((item) => item.id === currentId)
      : eligible[0];
  const providerMissing = !!selector && !provider;
  const configuredProvider = !selector || currentId === provider?.id;
  return {
    provider,
    providerMissing,
    enabled:
      groupEnabled &&
      (selection?.enabled ?? tool.default_enabled) &&
      !providerMissing &&
      configuredProvider,
  };
}
