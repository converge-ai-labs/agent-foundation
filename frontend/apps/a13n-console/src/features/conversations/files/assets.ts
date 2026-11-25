import { queryOptions, useMutation } from "@tanstack/react-query";
import type { Client } from "../../../service-client";
import { data, type Schema } from "../../../shared/api";
import { downloadBlob } from "../../../shared/download";

export type Asset = Schema["Asset"];
export type FileReference = {
  assetId: string;
  publication?: { runId: string; toolCallId: string; legacy: boolean };
};

export function assetQuery(
  client: Client,
  workspaceId: string,
  assetId: string,
) {
  return queryOptions({
    queryKey: ["asset", workspaceId, assetId],
    staleTime: 60_000,
    retry: false,
    queryFn: ({ signal }) =>
      client
        .workspace(workspaceId)
        .GET("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: assetId } },
          signal,
        })
        .then(data),
  });
}

export function matchesPublication(
  asset: Asset,
  publication: FileReference["publication"],
) {
  return (
    !publication ||
    (asset.source?.run_id === publication.runId &&
      asset.source.tool_call_id === publication.toolCallId)
  );
}

export function readAsset(
  client: Client,
  workspaceId: string,
  assetId: string,
  signal?: AbortSignal,
) {
  return client
    .workspace(workspaceId)
    .GET("/api/v1/assets/{asset_id}/content", {
      params: { path: { asset_id: assetId } },
      parseAs: "blob",
      signal,
    })
    .then(data);
}

export function useAssetDownload(
  client: Client,
  workspaceId: string,
  assetId: string,
  name: string,
) {
  return useMutation({
    mutationFn: async () =>
      downloadBlob(await readAsset(client, workspaceId, assetId), name),
  });
}
