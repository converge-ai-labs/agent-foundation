import { useState } from "react";
import { useLocation, useNavigate } from "react-router";
import type { Schema } from "../../shared/api";
import type { UsageGroup } from "./breakdown";

export type UsageAgent = Pick<Schema["AgentUsage"], "agent_id" | "name">;
export type UsageMember = Pick<
  Schema["MemberUsage"],
  "principal_id" | "name" | "kind" | "status"
>;

interface UsageView {
  workspaceId: string;
  range: { preset: number } | { start: string; end: string };
  now: string;
  metric: "cost" | "tokens";
  group: UsageGroup;
  agent: UsageAgent | null;
  member: UsageMember | null;
  breakdownPages: string[];
  sessionPages: string[];
}

/** Keep this visit's selections in its history entry, including across Debug navigation. */
export function useUsageView(workspaceId: string, admin: boolean) {
  const location = useLocation();
  const navigate = useNavigate();
  const [initial] = useState<UsageView>(() => ({
    workspaceId,
    range: { preset: 7 * 86400 },
    now: new Date().toISOString(),
    metric: "cost",
    group: "agents",
    agent: null,
    member: null,
    breakdownPages: [],
    sessionPages: [],
  }));
  const saved = location.state?.usageView as UsageView | undefined;
  const restored = saved?.workspaceId === workspaceId ? saved : initial;
  // Restored UI state never grants access to admin-only member analytics.
  const view =
    !admin && (restored.member || restored.group === "members")
      ? {
          ...restored,
          member: null,
          group:
            restored.group === "members" ? ("agents" as const) : restored.group,
          breakdownPages: [],
          sessionPages: [],
        }
      : restored;
  function update(change: Partial<UsageView>) {
    void navigate(location, {
      replace: true,
      preventScrollReset: true,
      state: { ...location.state, usageView: { ...view, ...change } },
    });
  }
  return { view, update };
}

export function usagePage(
  history: string[],
  onChange: (history: string[]) => void,
) {
  return {
    cursor: history.at(-1),
    next: (cursor: string) => onChange([...history, cursor]),
    previous: history.length ? () => onChange(history.slice(0, -1)) : undefined,
  };
}
