import { useQuery } from "@tanstack/react-query";
import { useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { readDocument } from "./documents";

/** Accepted instance defaults, shared by resource editors and lists. */
export function useRootConfiguration(enabled = true) {
  const { client } = useTransport();
  const sources = useSources();
  const root = sources.data?.sources.find(
    (item) => item.resource_kind === "root",
  );
  const query = useQuery({
    queryKey: ["source", root?.relative_path],
    enabled: enabled && !!root?.content_available,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: root!.relative_path } },
          signal,
        }),
      ),
  });
  return {
    ...query,
    path: root?.relative_path,
    configuration: readDocument(query.data?.content ?? "")?.toJS(),
  };
}
