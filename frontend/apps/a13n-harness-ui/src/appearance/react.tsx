import {
  createContext,
  useContext,
  useLayoutEffect,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { AppearanceStore, appearanceStore } from "./store";
const Context = createContext<AppearanceStore | null>(null);
export function AppearanceProvider({
  children,
  store = appearanceStore(),
}: {
  children: ReactNode;
  store?: AppearanceStore;
}) {
  useLayoutEffect(() => store.start(), [store]);
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
export function useAppearance() {
  const store = useContext(Context) ?? appearanceStore();
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return { ...snapshot, store };
}
