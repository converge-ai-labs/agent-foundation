import { useMutation, useQueryClient } from "@tanstack/react-query";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { refreshThread } from "./refresh";
import { refreshThreadLists } from "./queries";

export function useThreadArchive(
  thread: Schema<"ThreadSummary">,
  onSuccess?: (archived: boolean) => void,
) {
  const { client } = useTransport();
  const queries = useQueryClient();
  return useMutation({
    mutationFn: (archived: boolean) =>
      result(
        client.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: thread.thread_id } },
          body: {
            expected_version: thread.metadata_version,
            patch: { archived },
          },
        }),
      ),
    onSuccess: (_, archived) => onSuccess?.(archived),
    onSettled: () => {
      refreshThread(queries, thread.thread_id, "metadata");
      void refreshThreadLists(queries);
    },
  });
}
