import { createContext, useContext, useState, type ReactNode } from "react";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { useMutation } from "@tanstack/react-query";

type ControlState = {
  pending: boolean;
  unknown: boolean;
  error?: unknown;
  outcome?: Schema<"ChildControlResult">;
};
const empty: ControlState = { pending: false, unknown: false };
const ChildControls = createContext<{
  states: Record<string, ControlState>;
  update: (key: string, patch: Partial<ControlState>) => void;
} | null>(null);

/** Private control outcomes survive inspector and route changes. */
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

export function useChildControl(
  child: Schema<"ChildExecutionView">,
  reconcile: () => void,
) {
  const { client } = useTransport();
  const { state: controlState, update: updateControl } = useChildControlState(
    child.parent_thread_id,
    child.execution_id,
  );
  const path = {
    thread_id: child.parent_thread_id,
    execution_id: child.execution_id,
  };
  const control = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/threads/{thread_id}/children/{execution_id}/cancel", {
          params: { path },
        }),
      ),
    onMutate: () =>
      updateControl({ pending: true, error: undefined, outcome: undefined }),
    onSuccess: (outcome) => {
      if (outcome.execution_id !== child.execution_id) {
        updateControl({ unknown: true });
        return;
      }
      updateControl({ outcome });
    },
    onError: (error) =>
      updateControl({
        error,
        unknown: !(error instanceof ApiError) || error.status >= 500,
      }),
    onSettled: () => {
      updateControl({ pending: false });
      reconcile();
    },
  });
  return { controlState, updateControl, control };
}
