import { createContext, useContext, useState, type ReactNode } from "react";
import type { AppSession } from "./app-session";

/** Browser-local selection only. Other tabs and shared drafts never select these Views. */
export class AppContextSelection {
  private sessions = new Map<string, AppSession>();
  register(session: AppSession) {
    this.sessions.set(session.view.view_id, session);
    return () => {
      this.sessions.delete(session.view.view_id);
    };
  }
  capture(rootThreadId: string) {
    return [...this.sessions.values()].flatMap((session) => {
      if (session.view.root_thread_id !== rootThreadId) return [];
      const reference = session.captureContext();
      return reference ? [reference] : [];
    });
  }
}
const Context = createContext<AppContextSelection | undefined>(undefined);
export const useAppContextSelection = () => useContext(Context);
export function AppContextProvider({ children }: { children: ReactNode }) {
  const [selection] = useState(() => new AppContextSelection());
  return <Context value={selection}>{children}</Context>;
}
