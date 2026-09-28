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
import { ArchiveIcon, CopyIcon, DotsThreeIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { changeAgentImage } from "./images";
import { AgentAvatar } from "./avatar";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/forms";
import { ErrorNotice } from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";

export function AgentDetails({
  resource,
  reload,
  onImageSaved,
  close,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
  onImageSaved: () => Promise<void>;
  close: () => void;
}) {
  const [snapshot, setSnapshot] = useState(resource);
  const { value: agent, etag } = snapshot,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient();
  const [name, setName] = useState(agent.name),
    [description, setDescription] = useState(agent.description);
  const save = useMutation({
    mutationFn: async () => {
      if (!etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return client
        .workspace(workspace.id)
        .PATCH("/api/v1/agents/{agent_id}", {
          params: {
            path: { agent_id: agent.id },
          },
          headers: ifMatch(etag),
          body: { name, description },
        })
        .then(data);
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      await cache.invalidateQueries({
        queryKey: ["agent-by-id", workspace.id],
      });
      reload();
      close();
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
          disabled={save.isPending || image.isPending || !can("write")}
        >
          <div className={styles.stack}>
            <div className="mb-2">
              <ImagePicker
                hasImage={!!agent.image_url}
                editable={can("write")}
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
          </div>
          <FormActions
            pending={save.isPending}
            onCancel={close}
            disabled={name === agent.name && description === agent.description}
          />
        </fieldset>
        <ErrorNotice error={save.error ?? image.error} retry={reload} />
      </form>
    </div>
  );
}

export function AgentActions({
  resource,
  reload,
  leading,
  triggerVariant = "ghost",
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
  leading?: ReactNode;
  triggerVariant?: "ghost" | "outline";
}) {
  const { value: agent, etag } = resource,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const path = { agent_id: agent.id };
  const archive = async () => {
    if (!etag)
      throw new Error(
        t("Version information is unavailable. Reload this page."),
      );
    await client
      .workspace(workspace.id)
      .POST(
        agent.archived_at
          ? "/api/v1/agents/{agent_id}/unarchive"
          : "/api/v1/agents/{agent_id}/archive",
        { params: { path }, headers: ifMatch(etag) },
      )
      .then(data);
    reload();
  };
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            variant={triggerVariant}
            size={triggerVariant === "outline" ? "icon" : "icon-sm"}
            aria-label={t("More agent actions")}
            title={t("More agent actions")}
          />
        }
      >
        <DotsThreeIcon size={16} />
      </MenuTrigger>
      <MenuPopup align="end">
        {leading}
        {can("write") && agent.source === "custom" && (
          <Confirm
            subject={agent.name}
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
            action={archive}
          />
        )}
        {can("write") && !agent.archived_at && (
          <Confirm
            subject={agent.name}
            title={t("Duplicate agent")}
            description={t("Create an independent agent from this version.")}
            triggerElement={
              <MenuItem closeOnClick={false}>
                <CopyIcon size={13} />
                {t("Duplicate")}
              </MenuItem>
            }
            action={async () => {
              const name = `${agent.name} (${t("copy")})`;
              const result = await client
                .workspace(workspace.id)
                .POST("/api/v1/agents/{agent_id}/duplicate", {
                  params: { path },
                  body: { name },
                })
                .then(data);
              void cache.invalidateQueries();
              navigate(`${basePath}/agents/${result.id}`);
            }}
          />
        )}
      </MenuPopup>
    </Menu>
  );
}
