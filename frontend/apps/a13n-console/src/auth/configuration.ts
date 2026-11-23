import { useQuery } from "@tanstack/react-query";
import { data } from "../shared/api";
import { useClient } from "./context";

export function useAuthConfiguration(refreshOnMount = false) {
  const client = useClient();
  return useQuery({
    queryKey: ["auth-configuration"],
    refetchOnMount: refreshOnMount ? "always" : true,
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/auth/configuration", { signal }).then(data),
  });
}
