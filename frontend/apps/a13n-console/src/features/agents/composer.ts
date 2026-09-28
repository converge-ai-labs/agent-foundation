import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, type Schema } from "../../shared/api";
import { modelApi } from "../models/api";
import { providersPath } from "../providers/navigation";

function modelRequired(error: unknown) {
  return (
    error instanceof ApiError &&
    error.code === "conflict" &&
    error.details.reason === "model_required"
  );
}

/** The agent version a conversation with the composer starts from. */
export interface ComposerTarget {
  agent: Pick<Schema["Agent"], "id" | "name">;
  revision: Pick<Schema["AgentRevision"], "id" | "number">;
}

/**
 * Agent Composer is the workspace's builtin agent. Writers
 * prepare it, which creates it or brings it up to date, before conversing;
 * other runners converse with it once it exists. A conversation about an
 * existing agent opens with a first message naming it, which the user sends.
 */
export function useAgentComposer() {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    toast = useToast(),
    { workspace, basePath, can } = useWorkspace();
  const write = can("write");
  const existing = useQuery({
    queryKey: ["agents", workspace.id, "builtin"],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents", {
          params: { query: { source: "builtin" } },
          signal,
        })
        .then(data)
        .then((page) => page.items[0] ?? null),
    enabled: !write && can("run"),
  });
  const converse = (agentId: string, target?: ComposerTarget) => {
    const search = new URLSearchParams({ agent: agentId });
    if (target)
      search.set(
        "message",
        t(
          "Help me change the agent {{name}} (ID {{id}}), starting from its version {{version}} (revision ID {{revision}}).",
          {
            name: target.agent.name,
            id: target.agent.id,
            version: target.revision.number,
            revision: target.revision.id,
          },
        ),
      );
    navigate(`${basePath}/sessions/new?${search}`);
  };
  const prepare = useMutation({
    mutationFn: (_target?: ComposerTarget) =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/agent-composer", {})
        .then(data),
    onSuccess: (agent, target) => {
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      converse(agent.id, target);
    },
    onError: async (error) => {
      if (!modelRequired(error)) return;
      const configured = await modelApi(client, workspace.id)
        .providers(new AbortController().signal)
        .then(
          (page) => page.items.some((provider) => provider.enabled),
          () => false,
        );
      toast.add({
        type: "warning",
        title: t(
          configured
            ? "Provider configured. Add an enabled model to continue."
            : "Configure a model provider to start Agent Composer.",
        ),
        actionProps: {
          children: t(configured ? "Open Models" : "Open model setup"),
          onClick: () =>
            navigate(
              configured
                ? `${basePath}/models`
                : providersPath("models", workspace),
            ),
        },
      });
    },
  });
  const composer = existing.data?.source === "builtin" ? existing.data : null;
  return {
    available: write || !!composer,
    pending: prepare.isPending,
    /** Failures other than a missing model, which the setup notice covers. */
    error: modelRequired(prepare.error) ? null : prepare.error,
    start: (target?: ComposerTarget) =>
      write
        ? prepare.mutate(target)
        : composer && converse(composer.id, target),
  };
}
