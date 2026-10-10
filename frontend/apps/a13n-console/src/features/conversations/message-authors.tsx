import { useQueries } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useAuth, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";

type Author = Schema["MessageAuthor"];
interface Authors {
  register: (entryId: string) => void;
  entries: Map<string, Author>;
  duplicateNames: Set<string>;
  viewerId?: string;
  failed: Set<string>;
  retry: (entryId: string) => void;
}
const AuthorContext = createContext<Authors | null>(null);

/** Visible messages share bounded queries, including history inherited by a fork. */
export function SessionAuthorsProvider({
  sessionId,
  children,
}: {
  sessionId: string;
  children: ReactNode;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    auth = useAuth();
  const [ids, setIds] = useState<string[]>([]);
  const register = useCallback((id: string) => {
    setIds((previous) =>
      previous.includes(id) ? previous : [...previous, id],
    );
  }, []);
  const batches: string[][] = [];
  for (let index = 0; index < ids.length; index += 100)
    batches.push(ids.slice(index, index + 100));
  const queries = useQueries({
    queries: batches.map((batch) => ({
      queryKey: ["message-authors", workspace.id, sessionId, batch],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/sessions/{session_id}/message-authors", {
            params: {
              path: { session_id: sessionId },
              query: { entry_id: batch },
            },
            signal,
          })
          .then(data),
    })),
  });
  const entries = new Map(
    queries.flatMap((query) =>
      (query.data?.items ?? []).map(
        (author) => [author.entry_id, author] as const,
      ),
    ),
  );
  const names = new Map<string, Set<string>>();
  for (const { principal } of entries.values()) {
    if (!principal) continue;
    const same = names.get(principal.name) ?? new Set<string>();
    same.add(principal.id);
    names.set(principal.name, same);
  }
  const duplicateNames = new Set(
    [...names].filter(([, ids]) => ids.size > 1).map(([name]) => name),
  );
  const failed = new Set(
    queries.flatMap((query, index) => (query.isError ? batches[index]! : [])),
  );
  return (
    <AuthorContext.Provider
      value={{
        register,
        entries,
        duplicateNames,
        viewerId: auth.data?.user.value.id,
        failed,
        retry: (id) => {
          const index = batches.findIndex((batch) => batch.includes(id));
          if (index >= 0) void queries[index]!.refetch();
        },
      }}
    >
      {children}
    </AuthorContext.Provider>
  );
}

export function useMessageAuthor(entryId?: string | null) {
  const authors = useContext(AuthorContext),
    register = authors?.register;
  useEffect(() => {
    if (entryId) register?.(entryId);
  }, [entryId, register]);
  const author = entryId ? authors?.entries.get(entryId) : undefined;
  return {
    author,
    me: !!author?.principal && author.principal_id === authors?.viewerId,
    sameName:
      !!author?.principal &&
      !!authors?.duplicateNames.has(author.principal.name),
    failed: !!entryId && !!authors?.failed.has(entryId),
    retry: () => {
      if (entryId) authors?.retry(entryId);
    },
  };
}
