import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationKeys, conversationQueries } from "./api";
import { readEarlierDisplay, type RunDisplay } from "./display";

/** One shared committed window for live and historical Run presentations. */
export function useRunHistory(runId: string) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const key = conversationKeys(workspace.id).items(runId);
  const read = useQuery(conversationQueries(client, workspace.id).items(runId));
  const earlier = useMutation({
    mutationFn: async ({
      before,
    }: {
      before: number;
      beforeCommit: () => void;
    }) => {
      const current = cache.getQueryData<RunDisplay>(key);
      if (!current) throw new Error("Run content has not loaded.");
      return readEarlierDisplay(client, workspace.id, runId, current, before);
    },
    onSuccess: async (page, { beforeCommit }) => {
      // Cancel a concurrent refresh before merging: its older captured prefix
      // must not discard the history this request just added.
      await cache.cancelQueries({ queryKey: key, exact: true });
      beforeCommit();
      cache.setQueryData<RunDisplay>(key, (current) => {
        if (!current) return page;
        const first = current.items[0]?.ordinal ?? Infinity;
        return {
          ...current,
          items: [
            ...page.items.filter((item) => item.ordinal < first),
            ...current.items,
          ],
        };
      });
    },
  });
  return {
    read,
    earlier,
    loadEarlier: (before: number, beforeCommit: () => void) =>
      earlier.mutate({ before, beforeCommit }),
  };
}
