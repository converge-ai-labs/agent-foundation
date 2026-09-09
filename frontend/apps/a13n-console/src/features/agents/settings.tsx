import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input } from "a13n-ui";
import { Copy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function AgentSettings({
  resource,
  reload,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
}) {
  const [snapshot] = useState(resource);
  const { value: agent, etag } = snapshot,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    idempotency = useIdempotency();
  const [name, setName] = useState(agent.name),
    [description, setDescription] = useState(agent.description ?? ""),
    [environment, setEnvironment] = useState(
      agent.default_environment_template_id ?? "none",
    );
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
            params: {
              path: { workspace_id: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const save = useMutation({
    mutationFn: async () => {
      if (!etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      await client.http.PATCH("/api/v1/agents/{agent_id}", {
        params: { path: { agent_id: agent.id }, header: { "If-Match": etag } },
        headers: workspaceHeaders(workspace.id),
        body: {
          name,
          description: description || null,
          default_environment_template_id:
            environment === "none" ? null : environment,
        },
      });
    },
    onSuccess: reload,
  });
  const action = async (
    action: "enable" | "disable" | "archive" | "unarchive",
  ) => {
    if (!etag)
      throw new Error(
        t("Version information is unavailable. Reload this page."),
      );
    await client.http.POST("/api/v1/agents/{agent_id}/{action}", {
      params: {
        path: { agent_id: agent.id, action },
        header: {
          ...commandHeaders(
            workspace.id,
            idempotency.forBody({ action, etag }),
          ),
          "If-Match": etag,
        },
      },
    });
    idempotency.reset();
    reload();
  };
  return (
    <div className={styles.stack}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="fieldset-reset" disabled={!can("agent.update")}>
          <div className={styles.stack}>
            <FormField className="min-w-0 w-full" label={t("Name")}>
              <Input
                required={true}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <FormField className="min-w-0 w-full" label={t("Description")}>
              <Input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </FormField>
            <ChoiceField
              placeholder={t("Select environment")}
              value={environment}
              className="min-w-0"
              onValueChange={setEnvironment}
              label={t("Default environment")}
              options={[
                { value: "none", label: t("No default environment") },
                ...(templates.data?.map((item) => ({
                  value: item.id,
                  label: item.name,
                })) ?? []),
              ]}
            />
          </div>
          <FormActions pending={save.isPending} />
        </fieldset>
        <ErrorNotice error={save.error} retry={reload} />
      </form>
      {(can("agent.lifecycle") || can("agent.duplicate")) && (
        <div className={styles.actions}>
          {can("agent.lifecycle") && (
            <>
              <Confirm
                title={t(agent.enabled ? "Disable agent" : "Enable agent")}
                description={t("This changes whether new runs can start.")}
                trigger={t(agent.enabled ? "Disable" : "Enable")}
                action={() => action(agent.enabled ? "disable" : "enable")}
              />
              <Confirm
                title={t(
                  agent.archived_at ? "Unarchive agent" : "Archive agent",
                )}
                description={t(
                  "Archived agents leave the default list. Their history remains available.",
                )}
                trigger={t(agent.archived_at ? "Unarchive" : "Archive")}
                danger={!agent.archived_at}
                action={() =>
                  action(agent.archived_at ? "unarchive" : "archive")
                }
              />
            </>
          )}
          {can("agent.duplicate") && (
            <Confirm
              title={t("Duplicate agent")}
              description={t("Create an independent agent from this version.")}
              trigger={
                <>
                  <Copy size={13} />
                  {t("Duplicate")}
                </>
              }
              action={async () => {
                const body = {
                  expected_version: agent.version,
                  name: `${agent.name} (${t("copy")})`,
                };
                const result = data(
                  await client.http.POST(
                    "/api/v1/agents/{agent_id}/duplicate",
                    {
                      params: {
                        path: { agent_id: agent.id },
                        header: commandHeaders(
                          workspace.id,
                          idempotency.forBody(body),
                        ),
                      },
                      body,
                    },
                  ),
                );
                idempotency.reset();
                void cache.invalidateQueries();
                navigate(`/workspaces/${workspace.id}/agents/${result.id}`);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
