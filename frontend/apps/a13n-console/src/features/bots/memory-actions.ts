import type { RefObject } from "react";
import type { QueryClient } from "@tanstack/react-query";

export function refreshMemory(cache: QueryClient, accountId: string) {
  return cache.invalidateQueries({
    predicate: (query) =>
      typeof query.queryKey[0] === "string" &&
      query.queryKey[0].startsWith("bot-memory-") &&
      query.queryKey[1] === accountId,
  });
}

export type MemoryDialogControl = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  finalFocus: RefObject<HTMLButtonElement | null>;
};
