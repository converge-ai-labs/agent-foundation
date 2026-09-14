import type { Client } from "@converge.ai/a13n";
import { data, type Schema } from "../../shared/api";
export type ConnectorScope = { kind: "workspace" | "organization"; id: string };
export function connectorApi(client: Client, scope: ConnectorScope) {
  const organization_id = scope.id,
    workspace_id = scope.id;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      scope.kind === "organization"
        ? client.http
            .GET("/api/v1/organizations/{organization}/connector-providers", {
              params: {
                path: { organization: organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/connector-providers", {
              params: {
                path: { workspace: workspace_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data),
    create: (body: Schema["CreateConnectorProviderRequest"], key: string) =>
      scope.kind === "organization"
        ? client.http
            .POST("/api/v1/organizations/{organization}/connector-providers", {
              params: {
                path: { organization: organization_id },
                header: { "Idempotency-Key": key },
              },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/connector-providers", {
              params: {
                path: { workspace: workspace_id },
                header: { "Idempotency-Key": key },
              },
              body,
            })
            .then(data),
  };
}
