import { createContext, useContext, useState, type ReactNode } from "react";
import type { Schema } from "../transport/client";

type ControlState = {
  instruction: string;
  pending: boolean;
  unknown: boolean;
  error?: unknown;
  outcome?: Schema<"ChildControlResult">;
};
const empty: ControlState = { instruction: "", pending: false, unknown: false };
const ChildControls = createContext<{
  states: Record<string, ControlState>;
  update: (key: string, patch: Partial<ControlState>) => void;
} | null>(null);

/** Private, instance-local control drafts survive inspector and route changes. */
export function ChildControlsProvider({ children }: { children: ReactNode }) {
  const [states, setStates] = useState<Record<string, ControlState>>({});
  return (
    <ChildControls
      value={{
        states,
        update: (key, patch) =>
          setStates((current) => ({
            ...current,
            [key]: { ...(current[key] ?? empty), ...patch },
          })),
      }}
    >
      {children}
    </ChildControls>
  );
}
export function useChildControlState(parentId: string, executionId: string) {
  const shared = useContext(ChildControls);
  const [local, setLocal] = useState(empty);
  const key = JSON.stringify([parentId, executionId]);
  const state = shared?.states[key] ?? local;
  const update = (patch: Partial<ControlState>) => {
    if (shared) shared.update(key, patch);
    else setLocal((current) => ({ ...current, ...patch }));
  };
  return { state, update };
}
