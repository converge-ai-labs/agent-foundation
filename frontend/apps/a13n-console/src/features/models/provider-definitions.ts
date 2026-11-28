import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { realtimeModelApis } from "./model-options";
import { data, type Schema } from "../../shared/api";

export function useModelProviderDefinitions() {
  const client = useClient();
  return useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: "model" } },
          signal,
        })
        .then(data),
  });
}

export function useRequestModelChoices() {
  const definitions = useModelProviderDefinitions();
  const realtime = realtimeModelApis(definitions.data?.items);
  return {
    definitions,
    accepts: (model: Schema["Model"]) =>
      !!definitions.data && !realtime.has(model.config.model_api),
  };
}
