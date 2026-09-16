import { ApiError } from "../../service-client";
import type { QueryClient } from "@tanstack/react-query";

export function refreshMemory(cache: QueryClient, accountId: string) {
  return cache.invalidateQueries({
    predicate: (query) =>
      typeof query.queryKey[0] === "string" &&
      query.queryKey[0].startsWith("bot-memory-") &&
      query.queryKey[1] === accountId,
  });
}

export function unconfirmedWrite(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    error.status >= 500 ||
    ["memory_write_unconfirmed", "memory_operation_pending"].includes(
      error.code,
    )
  );
}
