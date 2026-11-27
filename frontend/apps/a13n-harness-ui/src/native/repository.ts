import { useQuery } from "@tanstack/react-query";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";

export function useGitRepository(
  path: string,
  refetchOnMount: true | "always" = true,
) {
  const { client } = useTransport();
  const feature = useStatus().data?.features?.host_git;
  const discovery = useQuery({
    queryKey: ["native", "repository", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/host/git/repository", {
          params: { query: { path } },
          signal,
        }),
      ),
    enabled: !!path && !!feature,
    refetchOnMount,
  });
  return { feature, discovery };
}

export function isLinkedWorktree(repository: Schema<"GitRepository">) {
  // Linked worktrees have a private Git directory alongside the shared common directory.
  return repository.git_dir !== repository.common_dir;
}
