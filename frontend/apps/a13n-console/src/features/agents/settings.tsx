import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "a13n-ui";
import {
  ArchiveIcon,
  CopyIcon,
  DotsThreeIcon,
  PowerIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { changeAgentImage } from "./images";
import { AgentAvatar } from "./avatar";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/image-picker";
import { ResourceKeyField } from "../../shared/resource-key";
import { ErrorNotice } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function AgentDetails({
  resource,
  reload,
  onImageSaved,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
  onImageSaved: () => Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState(resource);
  const { value: agent, etag } = snapshot,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [name, setName] = useState(agent.name),
    [key, setKey] = useState(agent.key),
    [description, setDescription] = useState(agent.description ?? "");
  const save = useMutation({
    mutationFn: async () => {
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
            name,
            key,
            description: description || null,
          },
        })
        .then(data);
    },
    onSuccess: async (result) => {
      await cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      await cache.invalidateQueries({
        queryKey: ["agent-by-id", workspace.id],
      });
      if (result.key !== agent.key) {
        cache.removeQueries({ queryKey: ["agent", workspace.id, agent.key] });
        navigate(`${basePath}/agents/${result.key}`, { replace: true });
      } else reload();
    },
  });
  const image = useMutation({
    mutationFn: async (file: File | null) => {
      if (!etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      if (file && file.size > MAX_IMAGE_BYTES)
        throw new Error(
          t("Choose a PNG, JPEG, or WebP image smaller than 5 MB."),
        );
      return changeAgentImage(client, workspace.id, agent.id, etag, file);
    },
    onSuccess: async (result) => {
      setSnapshot(result);
      await onImageSaved();
      await cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      await cache.invalidateQueries({
        queryKey: ["agent-by-id", workspace.id],
      });
    },
  });
  return (
    <div className={styles.stack}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset
          className="fieldset-reset"
          disabled={save.isPending || image.isPending || !can("agent.update")}
        >
          <div className={styles.stack}>
            <div className="mb-2">
              <ImagePicker
                hasImage={!!agent.image_url}
                editable={can("agent.update")}
                pending={save.isPending || image.isPending}
                onChange={(file) => image.mutate(file)}
                description={
                  image.isPending ? (
                    <span role="status">{t("Saving…")}</span>
                  ) : (
                    t("PNG, JPEG, or WebP. Up to 5 MB.")
                  )
                }
              >
                <AgentAvatar
                  name={name}
                  id={agent.id}
                  url={agent.image_url}
                  className="size-16 rounded-xl text-2xl"
                />
              </ImagePicker>
            </div>
            <FormField className="min-w-0 w-full" label={t("Name")}>
              <Input
                required={true}
                maxLength={128}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <FormField className="min-w-0 w-full" label={t("Description")}>
              <Input
                maxLength={4096}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </FormField>
            <ResourceKeyField
              value={key}
              onChange={setKey}
              disabled={save.isPending}
            />
          </div>
          <FormActions pending={save.isPending} />
        </fieldset>
        <ErrorNotice error={save.error ?? image.error} retry={reload} />
      </form>
    </div>
  );
}

export function AgentActions({
  resource,
  reload,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
}) {
  const { value: agent, etag } = resource,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    idempotency = useIdempotency();
  const action = async (
    action: "enable" | "disable" | "archive" | "unarchive",
  ) => {
    if (!etag)
      throw new Error(
        t("Version information is unavailable. Reload this page."),
      );
    await client.http.POST(
      "/api/v1/workspaces/{workspace}/agents/{agent}/{action}",
      {
        params: {
          path: { workspace: workspace.id, agent: agent.id, action },
          header: {
            ...commandHeaders(
              workspace.id,
              idempotency.forBody({ action, etag }),
            ),
            "If-Match": etag,
          },
        },
      },
    );
    idempotency.reset();
    reload();
  };
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("More agent actions")}
            title={t("More agent actions")}
          />
        }
      >
        <DotsThreeIcon size={16} />
      </MenuTrigger>
      <MenuPopup align="end">
        {can("agent.lifecycle") && (
          <>
            <Confirm
              title={t(agent.enabled ? "Disable agent" : "Enable agent")}
              description={t("This changes whether new runs can start.")}
              triggerElement={
                <MenuItem closeOnClick={false}>
                  <PowerIcon size={14} />
                  {t(agent.enabled ? "Disable" : "Enable")}
                </MenuItem>
              }
              action={() => action(agent.enabled ? "disable" : "enable")}
            />
            <Confirm
              title={t(agent.archived_at ? "Unarchive agent" : "Archive agent")}
              description={t(
                "Archived agents leave the default list. Their history remains available.",
              )}
              triggerElement={
                <MenuItem
                  closeOnClick={false}
                  variant={agent.archived_at ? "default" : "destructive"}
                >
                  <ArchiveIcon size={14} />
                  {t(agent.archived_at ? "Unarchive" : "Archive")}
                </MenuItem>
              }
              danger={!agent.archived_at}
              action={() => action(agent.archived_at ? "unarchive" : "archive")}
            />
          </>
        )}
        {can("agent.duplicate") && (
          <Confirm
            title={t("Duplicate agent")}
            description={t("Create an independent agent from this version.")}
            triggerElement={
              <MenuItem closeOnClick={false}>
                <CopyIcon size={13} />
                {t("Duplicate")}
              </MenuItem>
            }
            action={async () => {
              const body = {
                expected_version: agent.version,
                name: `${agent.name} (${t("copy")})`,
              };
              const result = data(
                await client.http.POST(
                  "/api/v1/workspaces/{workspace}/agents/{agent}/duplicate",
                  {
                    params: {
                      path: { workspace: workspace.id, agent: agent.id },
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
              navigate(`${basePath}/agents/${result.key}`);
            }}
          />
        )}
      </MenuPopup>
    </Menu>
  );
}
