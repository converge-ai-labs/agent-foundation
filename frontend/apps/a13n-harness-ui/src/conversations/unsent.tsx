import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { result } from "../transport/client";
import { useTransport } from "../transport/context";
import type { ThreadDraft } from "./draft";
import { UnsentStore, unsentInputs } from "./unsent-store";

type UnsentState = {
  store?: UnsentStore;
  inputs: ReadonlyMap<string, string>;
};
const UnsentContext = createContext<UnsentState>({ inputs: new Map() });
const UnsentStoreContext = createContext<UnsentStore | undefined>(undefined);
export const useUnsent = () => useContext(UnsentContext);

export function useTrackUnsent(threadId: string, draft: ThreadDraft) {
  const store = useContext(UnsentStoreContext);
  useEffect(() => {
    store?.track(threadId, draft);
  }, [store, threadId, draft]);
}

export function UnsentProvider({ children }: { children: ReactNode }) {
  const transport = useTransport();
  const store = useMemo(() => new UnsentStore(), [transport]);
  const local = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => () => store.dispose(), [store]);
  const shared = useQuery({
    queryKey: ["unsent-drafts"],
    queryFn: ({ signal }) =>
      result(transport.client.GET("/api/drafts", { signal })),
  });
  const inputs = useMemo(
    () => unsentInputs(shared.data ?? [], local),
    [shared.data, local],
  );
  return (
    <UnsentStoreContext value={store}>
      <UnsentContext
        value={{
          store,
          inputs,
        }}
      >
        {children}
      </UnsentContext>
    </UnsentStoreContext>
  );
}
