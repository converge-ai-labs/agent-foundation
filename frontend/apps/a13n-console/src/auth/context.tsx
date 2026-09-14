import { createClient, type Client } from "@converge.ai/a13n";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  data,
  isUnauthorized,
  representation,
  type Schema,
} from "../shared/api";

export interface IdentityData {
  user: ReturnType<typeof representation<Schema["User"]>>;
  organizations: Schema["Organization"][];
}

const ClientContext = createContext<Client | null>(null);
const AuthContext = createContext<ReturnType<typeof useIdentity> | null>(null);
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (query.queryKey[0] !== "identity") revalidateSession(error);
    },
  }),
  mutationCache: new MutationCache({ onError: revalidateSession }),
  defaultOptions: {
    queries: { retry: false, staleTime: 15_000 },
    mutations: { retry: false },
  },
});
export function revalidateSession(error: unknown) {
  if (
    isUnauthorized(error) &&
    !isUnauthorized(queryClient.getQueryState(["identity"])?.error)
  )
    void queryClient.invalidateQueries({ queryKey: ["identity"] });
}

function useIdentity(client: Client, renew: () => void) {
  const query = useQuery<IdentityData>({
    queryKey: ["identity"],
    queryFn: async ({ signal }) => {
      const [user, csrf, organizations] = await Promise.all([
        client.http.GET("/api/v1/users/me", { signal }).then(representation),
        client.http.GET("/api/v1/auth/csrf", { signal }).then(data),
        client.http.GET("/api/v1/organizations", { signal }).then(data),
      ]);
      client.setCsrfToken(csrf.csrf_token);
      return { user, organizations: organizations.items };
    },
  });
  useEffect(() => {
    if (!isUnauthorized(query.error)) return;
    client.setCsrfToken(undefined);
    const otherQueries = {
      predicate: (query: { queryKey: readonly unknown[] }) =>
        query.queryKey[0] !== "identity",
    };
    void queryClient.cancelQueries(otherQueries);
    queryClient.removeQueries(otherQueries);
    queryClient.getMutationCache().clear();
  }, [client, query.error]);
  return {
    ...query,
    anonymous: isUnauthorized(query.error),
    refresh: () => queryClient.invalidateQueries({ queryKey: ["identity"] }),
    authenticated: (csrf: string) => {
      client.setCsrfToken(csrf);
      queryClient.clear();
      void query.refetch();
    },
    logout: async () => {
      try {
        await client.http.POST("/api/v1/auth/logout");
      } catch (error) {
        if (!isUnauthorized(error)) throw error;
      }
      client.close();
      queryClient.clear();
      renew();
    },
  };
}
function Identity({
  children,
  renew,
}: {
  children: ReactNode;
  renew: () => void;
}) {
  const identity = useIdentity(useClient(), renew);
  return (
    <AuthContext.Provider value={identity}>{children}</AuthContext.Provider>
  );
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [client, setClient] = useState(() =>
    createClient({
      baseUrl: window.location.origin,
      auth: { type: "session" },
    }),
  );
  useEffect(() => () => client.close(), [client]);
  return (
    <QueryClientProvider client={queryClient}>
      <ClientContext.Provider value={client}>
        <Identity
          renew={() =>
            setClient(
              createClient({
                baseUrl: window.location.origin,
                auth: { type: "session" },
              }),
            )
          }
        >
          {children}
        </Identity>
      </ClientContext.Provider>
    </QueryClientProvider>
  );
}
export function useClient() {
  const client = useContext(ClientContext);
  if (!client) throw new Error("Missing client provider");
  return client;
}
export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("Missing identity provider");
  return auth;
}
