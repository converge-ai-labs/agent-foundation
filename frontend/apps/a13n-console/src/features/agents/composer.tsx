import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { allPages, data, type Schema } from "../../shared/api";
import { modelApi } from "../models/api";
import { AddModel } from "../models/add-model";

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
  context?: string;
}

/**
 * Agent Composer is the workspace's composer preset. Writers
 * prepare it, which creates it or brings it up to date, before conversing;
 * other runners converse with it once it exists. A conversation about an
 * existing agent opens with a first message naming it, which the user sends.
 */
export function useAgentComposer() {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    { workspace, basePath, can } = useWorkspace();
  const write = can("write");
  const [setup, setSetup] = useState<{ target?: ComposerTarget }>();
  const existing = useQuery({
    queryKey: ["agents", workspace.id, "builtin"],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents", {
          params: { query: { source: "builtin", preset_kind: "composer" } },
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
        ) + (target.context ? `\n\n${target.context}` : ""),
      );
    navigate(`${basePath}/sessions/new?${search}`);
  };
  const prepare = useMutation({
    mutationFn: async (_target?: ComposerTarget) => {
      const api = modelApi(client, workspace.id);
      const signal = new AbortController().signal;
      const [models, providers] = await Promise.all([
        allPages((cursor) => api.models(signal, cursor)),
        allPages((cursor) => api.providers(signal, cursor)),
      ]);
      const enabledProviders = new Set(
        providers
          .filter((provider) => provider.enabled)
          .map((provider) => provider.id),
      );
      if (
        !models.some(
          (model) => model.enabled && enabledProviders.has(model.provider_id),
        )
      )
        return null;
      return client
        .workspace(workspace.id)
        .POST("/api/v1/agent-composer", {})
        .then(data);
    },
    onSuccess: (agent, target) => {
      if (!agent) {
        setSetup({ target });
        return;
      }
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      converse(agent.id, target);
    },
    onError: (error, target) => {
      // The server remains authoritative if availability changes after the check.
      if (modelRequired(error)) setSetup({ target });
    },
  });
  const composer = existing.data?.source === "builtin" ? existing.data : null;
  return {
    available: write || !!composer,
    pending: prepare.isPending,
    setup: setup ? (
      <AddModel
        controlledOpen
        requireEnabled
        submitLabel={t("Continue")}
        onClose={() => setSetup(undefined)}
        onSaved={() => {
          const target = setup.target;
          setSetup(undefined);
          prepare.mutate(target);
        }}
      />
    ) : null,
    /** Missing dependencies open setup; request failures remain visible. */
    error: modelRequired(prepare.error) ? null : prepare.error,
    start: (target?: ComposerTarget) =>
      write
        ? prepare.mutate(target)
        : composer && converse(composer.id, target),
  };
}
