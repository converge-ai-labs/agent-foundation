import { createContext } from "react";

export type SourceDraft = {
  content: string;
  base: string | null;
  digest: string | null;
  replacement: boolean;
};
// Deliberately memory-only. Kept above the auth gate so reauthentication does not discard edits.
export const DraftContext = createContext<Map<string, SourceDraft>>(new Map());
