import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import {
  allPages,
  data,
  ifMatch,
  representation,
  type Schema,
} from "../../shared/api";

export type Dashboard = Schema["Dashboard"];
export type DashboardConfig = Dashboard["config"];
export type Panel = NonNullable<DashboardConfig["panels"]>[number];
export type PanelQuery = Panel["queries"][number];
export type Source = Schema["DataSource"];
export type Target = { kind: "environment" | "device"; id: string };
export type TimeRange = { start: number; end: number };
// Infer the transport's readable response (openapi-fetch maps tuple arrays).
export type MetricsResult = Awaited<
  ReturnType<ReturnType<typeof monitoringApi>["query"]>
>;
export const queryStep = (range: TimeRange) =>
  Math.max(60, Math.ceil((range.end - range.start) / 600 / 60) * 60);

export function monitoringApi(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId);
  return {
    dashboards: () =>
      queryOptions({
        queryKey: ["metrics-dashboards", workspaceId],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            http
              .GET("/api/v1/metrics-dashboards", {
                params: { query: { cursor, limit: 100 } },
                signal,
              })
              .then(data),
          ),
      }),
    sources: () =>
      queryOptions({
        queryKey: ["metrics-data-sources", workspaceId],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            http
              .GET("/api/v1/metrics-data-sources", {
                params: { query: { cursor, limit: 100 } },
                signal,
              })
              .then(data),
          ),
      }),
    deploymentPreset: () =>
      queryOptions({
        queryKey: ["metrics-deployment-preset", workspaceId],
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/metrics-data-sources/deployment-preset", { signal })
            .then(data),
        staleTime: 0,
      }),
    capabilities: () =>
      queryOptions({
        queryKey: ["metrics-capabilities", workspaceId],
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/metrics-data-sources/capabilities", { signal })
            .then(data),
      }),
    preset: () => http.GET("/api/v1/metrics-dashboards/preset").then(data),
    save: (id: string, body: Schema["DashboardUpdate"], etag: string) =>
      http
        .PATCH("/api/v1/metrics-dashboards/{dashboard_id}", {
          params: { path: { dashboard_id: id } },
          body,
          headers: ifMatch(etag),
        })
        .then(representation),
    create: (body: Schema["DashboardCreate"]) =>
      http.POST("/api/v1/metrics-dashboards", { body }).then(representation),
    remove: (id: string, etag: string) =>
      http.DELETE("/api/v1/metrics-dashboards/{dashboard_id}", {
        params: { path: { dashboard_id: id } },
        headers: ifMatch(etag),
      }),
    async query(
      query: PanelQuery,
      target: Target | null,
      range: TimeRange,
      signal: AbortSignal,
    ) {
      // Bound each chart to about 600 evaluation instants, with minute-aligned cache resolution.
      const step = queryStep(range);
      if (query.kind === "promql")
        return http
          .POST("/api/v1/metrics-data-sources/{source_id}/query", {
            params: { path: { source_id: query.source_id } },
            signal,
            body: {
              kind: "range",
              expression: query.expression,
              ...range,
              step,
            },
          })
          .then(data);
      if (!target)
        throw new Error("Select an Environment or Device to query metrics.");
      const body = {
        metric: query.metric,
        history: query.history,
        ...range,
        step,
      };
      if (target.kind === "environment")
        return http
          .POST("/api/v1/environments/{environment_id}/metrics/query", {
            params: { path: { environment_id: target.id } },
            body,
            signal,
          })
          .then(data);
      return http
        .POST("/api/v1/devices/{device_id}/metrics/query", {
          params: { path: { device_id: target.id } },
          body,
          signal,
        })
        .then(data);
    },
  };
}
