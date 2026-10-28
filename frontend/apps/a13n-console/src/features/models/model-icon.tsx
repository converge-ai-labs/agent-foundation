import { HardDrivesIcon } from "@phosphor-icons/react";
import { BrandIcon, modelBrand, Skeleton } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import { InlineLoading } from "../../shared/feedback";
import { ProviderIcon } from "../../shared/identity";
import { workspaceModelsQuery, workspaceProvidersQuery } from "./api";

export { modelBrand } from "a13n-ui";

type ProviderIdentity = Pick<Schema["Provider"], "type" | "config">;

/**
 * The brand a Provider's Models fall back to. An OpenAI Provider with its own
 * base URL is an OpenAI-compatible endpoint, not OpenAI, so it has none.
 */
export function providerBrand(provider?: ProviderIdentity) {
  if (!provider) return undefined;
  return provider.type === "openai" && provider.config.base_url
    ? undefined
    : provider.type;
}

/** A Model Provider's mark: its brand, or a neutral endpoint glyph. */
export function ModelProviderIcon({
  provider,
}: {
  provider: ProviderIdentity;
}) {
  const brand = providerBrand(provider);
  return brand ? (
    <ProviderIcon type={brand} />
  ) : (
    <HardDrivesIcon
      aria-hidden="true"
      className="size-5 shrink-0 text-muted-foreground"
    />
  );
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
  // The catalog identity names the family even behind a gateway alias.
  const identity = modelBrand(
    catalogRef ? `${catalogRef.provider}/${catalogRef.model}` : upstream,
    provider,
  );
  return <BrandIcon identity={identity} alias={identity} size={size} />;
}

/**
 * The workspace's model with this key and the brand of the provider serving
 * it, pending until both lists have loaded.
 */
function useWorkspaceModel(modelKey: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  const providers = useQuery(workspaceProvidersQuery(client, workspace.id));
  const model = models.data?.find((entry) => entry.key === modelKey);
  return {
    model,
    provider: providerBrand(
      providers.data?.find((entry) => entry.id === model?.provider_id),
    ),
    isPending: models.isPending || providers.isPending,
  };
}

function WorkspaceModelIcon({
  entry: { model, provider, isPending },
  size,
}: {
  entry: ReturnType<typeof useWorkspaceModel>;
  size: number;
}) {
  return isPending ? (
    <Skeleton
      aria-hidden="true"
      className="shrink-0"
      style={{ width: size, height: size }}
    />
  ) : (
    <ModelIcon
      upstream={model?.config.model_name ?? ""}
      catalogRef={model?.catalog_ref}
      provider={provider}
      size={size}
    />
  );
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
  return <WorkspaceModelIcon entry={useWorkspaceModel(modelKey)} size={size} />;
}

/**
 * A configuration's model: its brand and display name, or the key alone when
 * the workspace no longer has it. The key stays on hover.
 */
export function ModelKeyLabel({
  modelKey,
  size = 14,
  className,
}: {
  modelKey: string;
  size?: number;
  className?: string;
}) {
  const entry = useWorkspaceModel(modelKey);
  return (
    <span className={className} title={modelKey}>
      <WorkspaceModelIcon entry={entry} size={size} />
      {entry.isPending ? (
        // About as wide as the name that replaces it, so the row holds still.
        <InlineLoading width={`${Math.min(modelKey.length, 16)}ch`} />
      ) : (
        <span>{entry.model?.name ?? modelKey}</span>
      )}
    </span>
  );
}
