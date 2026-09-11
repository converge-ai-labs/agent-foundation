import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  WarningCircleIcon,
  CheckIcon,
  CircleNotchIcon,
} from "@phosphor-icons/react";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./agents.module.css";
import { EditorSection } from "./section";

export function AgentEnvironment({
  resource,
  disabled,
  onSaved,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  disabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const { value: agent, etag } = resource;
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient();
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/environment-templates", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const save = useMutation({
    mutationFn: async (value: string) => {
      if (!etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return client.http
        .PATCH("/api/v1/workspaces/{workspace}/agents/{agent}", {
          params: {
            path: { workspace: workspace.id, agent: agent.id },
            header: { "If-Match": etag },
          },
          headers: workspaceHeaders(workspace.id),
          body: {
            default_environment_template_id: value === "none" ? null : value,
          },
        })
        .then(data);
    },
    onSuccess: async () => {
      await onSaved();
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      void cache.invalidateQueries({ queryKey: ["agent-by-id", workspace.id] });
    },
  });
  const selected = agent.default_environment_template_id;
  return (
    <EditorSection
      title={t("Default environment")}
      description={
        <>
          {t("Used for new sessions.")}
          <span className={styles.autoSaveNotice} role="status">
            {save.isPending ? (
              <CircleNotchIcon size={13} className="animate-spin" />
            ) : save.isError ? (
              <WarningCircleIcon size={13} />
            ) : (
              <CheckIcon size={13} />
            )}
            {t(
              save.isPending
                ? "Saving…"
                : save.isError
                  ? "Not saved"
                  : save.isSuccess
                    ? "Saved automatically"
                    : "Saves automatically",
            )}
          </span>
        </>
      }
    >
      <ChoiceField
        label={t("Default environment")}
        hideLabel
        disabled={disabled || save.isPending}
        readOnly={!can("agent.update")}
        value={save.isPending ? save.variables : (selected ?? "none")}
        onValueChange={(value) => save.mutate(value)}
        options={[
          { value: "none", label: t("No default environment") },
          ...(templates.data ?? []).map((item) => ({
            value: item.id,
            label: item.name,
          })),
          ...(selected && !templates.data?.some((item) => item.id === selected)
            ? [{ value: selected, label: selected }]
            : []),
        ]}
      />
      <ErrorNotice
        error={save.error ?? templates.error}
        retry={() => {
          if (save.error && save.variables !== undefined)
            save.mutate(save.variables);
          else void templates.refetch();
        }}
      />
    </EditorSection>
  );
}
