import { queryOptions } from "@tanstack/react-query";
import type { Client, paths } from "../../service-client";
import { allPages, data, rowTag, type Schema } from "../../shared/api";

export type Schedule = Schema["Schedule"];
export type Occurrence = Schema["OccurrenceSummary"];
export type ScheduleFilters = Pick<
  NonNullable<paths["/api/v1/schedules"]["get"]["parameters"]["query"]>,
  "state" | "q"
>;

export function scheduleKeys(workspaceId: string) {
  const root = ["schedules", workspaceId] as const;
  const schedule = (id: string) => [...root, id] as const;
  return {
    root,
    schedule,
    detail: (id: string) => [...schedule(id), "detail"] as const,
    status: (id: string) => [...schedule(id), "status"] as const,
    history: (id: string) => [...schedule(id), "history"] as const,
  };
}

export function scheduleQueries(client: Client, workspaceId: string) {
  const api = client.workspace(workspaceId),
    keys = scheduleKeys(workspaceId);
  return {
    page: (filters: ScheduleFilters, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.root, "list", filters, cursor],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/schedules", {
              params: { query: { ...filters, cursor, limit: 20 } },
              signal,
            })
            .then(data),
      }),
    detail: (schedule_id: string) =>
      queryOptions({
        queryKey: keys.detail(schedule_id),
        structuralSharing: (previous, next) => {
          const before = previous as Schedule | undefined,
            after = next as Schedule;
          return before && before.version > after.version ? before : after;
        },
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/schedules/{schedule_id}", {
              params: { path: { schedule_id } },
              signal,
            })
            .then(data),
      }),
    status: (schedule_id: string) =>
      queryOptions({
        queryKey: keys.status(schedule_id),
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/schedules/{schedule_id}/status", {
              params: { path: { schedule_id } },
              signal,
            })
            .then(data),
      }),
    history: (schedule_id: string, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.history(schedule_id), cursor],
        structuralSharing: (previous, next) => {
          const before = previous as Schema["OccurrencePage"] | undefined,
            after = next as Schema["OccurrencePage"];
          const held = new Map(before?.items.map((item) => [item.id, item]));
          return {
            ...after,
            items: after.items.map((item) => {
              const current = held.get(item.id);
              if (!current || current.version < item.version) return item;
              if (current.version > item.version) return current;
              // Submission versions stop changing after dispatch. Execution observations do not.
              const execution = current.execution;
              return execution && !item.execution
                ? { ...item, execution }
                : item;
            }),
          };
        },
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/schedules/{schedule_id}/occurrences", {
              params: {
                path: { schedule_id },
                query: { cursor, limit: 20, include: "execution" },
              },
              signal,
            })
            .then(data),
      }),
    agents: () =>
      queryOptions({
        queryKey: ["schedule-agents", workspaceId],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            api
              .GET("/api/v1/agents", {
                params: { query: { cursor, limit: 100 } },
                signal,
              })
              .then(data),
          ),
      }),
  };
}

export function scheduleCommands(
  client: Client,
  workspaceId: string,
  schedule: Schedule,
) {
  const api = client.workspace(workspaceId);
  const params = {
    path: { schedule_id: schedule.id },
    header: { "If-Match": rowTag(schedule) },
  };
  return {
    enable: () =>
      api
        .POST("/api/v1/schedules/{schedule_id}/enable", { params, body: {} })
        .then(data),
    pause: () =>
      api
        .POST("/api/v1/schedules/{schedule_id}/pause", { params, body: {} })
        .then(data),
    archive: () => api.DELETE("/api/v1/schedules/{schedule_id}", { params }),
    dispatch: (key: string) =>
      api
        .POST("/api/v1/schedules/{schedule_id}/dispatch", {
          params: {
            ...params,
            header: { ...params.header, "Idempotency-Key": key },
          },
          body: {},
        })
        .then(data),
    update: (body: Schema["ScheduleUpdate"]) =>
      api.PATCH("/api/v1/schedules/{schedule_id}", { params, body }).then(data),
  };
}
