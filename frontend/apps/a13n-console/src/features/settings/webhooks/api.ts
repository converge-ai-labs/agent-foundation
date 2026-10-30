import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../../service-client";
import { allPages, data, type Schema } from "../../../shared/api";

export type Subscription = Schema["Subscription"];
export type Delivery = Schema["WebhookDelivery"];

export function webhookKeys(workspaceId: string) {
  const root = ["webhooks", workspaceId] as const;
  return {
    root,
    list: [...root, "list"] as const,
    subscription: (id: string) => [...root, "subscription", id] as const,
    deliveries: (id: string) => [...root, "deliveries", id] as const,
  };
}

export function webhookQueries(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId);
  const keys = webhookKeys(workspaceId);
  return {
    list: (cursor?: string) =>
      queryOptions({
        queryKey: [...keys.list, cursor],
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/subscriptions", {
              signal,
              params: { query: { cursor, limit: 30 } },
            })
            .then(data),
      }),
    subscription: (id: string) =>
      queryOptions({
        queryKey: keys.subscription(id),
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/subscriptions/{subscription_id}", {
              signal,
              params: { path: { subscription_id: id } },
            })
            .then(data),
      }),
    deliveries: (id: string, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.deliveries(id), cursor],
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/subscriptions/{subscription_id}/deliveries", {
              signal,
              params: {
                path: { subscription_id: id },
                query: { cursor, limit: 30 },
              },
            })
            .then(data),
      }),
    agents: () =>
      queryOptions({
        queryKey: [...keys.root, "agents"],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            http
              .GET("/api/v1/agents", {
                signal,
                params: { query: { cursor, limit: 100 } },
              })
              .then(data),
          ),
      }),
  };
}
