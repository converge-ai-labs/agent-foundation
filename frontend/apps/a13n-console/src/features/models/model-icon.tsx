import { BrandIcon, brands, resolveBrand } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { workspaceModelsQuery } from "./api";

const identities = Object.keys(brands).sort((a, b) => b.length - a.length);

/** Prefer the model family over the gateway hosting it. */
export function modelBrand(
  upstream: string,
  provider?: string,
): string | undefined {
  const parts = upstream.trim().toLowerCase().split(/[/.]/).reverse();
  for (const part of parts) {
    if (resolveBrand({ identity: part, alias: part })) return part;
    const family = identities.find(
      (identity) =>
        part.startsWith(identity) &&
        /^[\d._:-]/.test(part.slice(identity.length)),
    );
    if (family) return family;
    if (/^glm[-\d]/.test(part)) return "zhipu";
    if (/^(doubao[-.]|seed[-\d])/.test(part)) return "doubao";
    if (/^(gpt[-\d]|o[134](?:[-.]|$))/.test(part)) return "openai";
  }
  return provider;
}

export function ModelIcon({
  upstream,
  provider,
  catalogRef,
  size = 32,
}: {
  upstream: string;
  provider?: string;
  catalogRef?: { provider: string; model: string } | null;
  size?: number;
}) {
  const identity =
    catalogRef === null
      ? undefined
      : modelBrand(
          catalogRef ? `${catalogRef.provider}/${catalogRef.model}` : upstream,
          provider,
        );
  return <BrandIcon identity={identity} alias={identity} size={size} />;
}

/**
 * A model named only by its key, as usage records name it: the workspace's
 * model decides the brand, as on the Models page, and an unknown key has none.
 */
export function ModelKeyIcon({
  modelKey,
  size = 16,
}: {
  modelKey: string | null;
  size?: number;
}) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  const model = models.data?.find((entry) => entry.key === modelKey);
  return (
    <ModelIcon
      upstream={model?.config.model_name ?? ""}
      catalogRef={model ? model.catalog_ref : null}
      size={size}
    />
  );
}
