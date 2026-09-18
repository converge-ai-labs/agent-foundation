import {
  Button,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  ModalFrame,
} from "a13n-ui";
import {
  DotsThreeOutlineVerticalIcon,
  DownloadSimpleIcon,
  PencilSimpleIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../../shared/api";
import { Confirm } from "../../../shared/dialogs";
import { ErrorNotice, ErrorToast } from "../../../shared/feedback";
import { FormActions } from "../../../shared/forms";
import shared from "../../../shared/shared.module.css";
import { useArchiveDownload } from "../archive";

export type SkillResource = ReturnType<typeof representation<Schema["Skill"]>>;

/**
 * Everything that changes or removes the skill itself, behind one trigger so
 * the header keeps a single primary action.
 */
export function SkillMenu({
  resource,
  revisionId,
  version,
}: {
  resource: SkillResource;
  revisionId: string;
  version: number;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { basePath, can } = useWorkspace(),
    navigate = useNavigate();
  const skill = resource.value;
  const [renaming, setRenaming] = useState(false);
  const download = useArchiveDownload(
    { id: revisionId, skill_id: skill.id, workspace_id: skill.workspace_id },
    `${skill.key}-v${version}.zip`,
  );
  return (
    <>
      <Menu>
        <MenuTrigger
          render={
            <Button
              variant="outline"
              size="icon"
              type="button"
              aria-label={t("More skill actions")}
              title={t("More skill actions")}
            />
          }
        >
          <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
        </MenuTrigger>
        <MenuPopup align="end">
          {can("skill.update") && (
            <MenuItem closeOnClick={true} onClick={() => setRenaming(true)}>
              <PencilSimpleIcon size={14} />
              {t("Rename skill")}
            </MenuItem>
          )}
          <MenuItem onClick={() => download.mutate()}>
            <DownloadSimpleIcon size={14} />
            {t("Download ZIP")}
          </MenuItem>
          {can("skill.delete") && (
            <>
              <MenuSeparator />
              <Confirm
                subject={skill.name}
                title={t("Delete skill")}
                description={t(
                  "This removes the skill and its revisions from ordinary access. Agents currently using this skill must be updated first.",
                )}
                danger
                triggerElement={
                  <MenuItem closeOnClick={false} variant="destructive">
                    <TrashIcon size={14} />
                    {t("Delete skill")}
                  </MenuItem>
                }
                action={async () => {
                  if (!resource.etag)
                    throw new Error(
                      t(
                        "Version information is unavailable. Reload this page.",
                      ),
                    );
                  await client.http.DELETE("/api/v1/skills/{skill_id}", {
                    params: {
                      path: { skill_id: skill.id },
                      header: {
                        ...workspaceHeaders(skill.workspace_id),
                        "If-Match": resource.etag,
                      },
                    },
                  });
                  navigate(`${basePath}/skills`);
                }}
              />
            </>
          )}
        </MenuPopup>
      </Menu>
      <ErrorToast error={download.error} />
      <ModalFrame
        open={renaming}
        onOpenChange={setRenaming}
        title={t("Rename skill")}
        description={t(
          "Change the display name without changing the skill key or its published versions.",
        )}
        closeLabel={t("Close")}
      >
        {renaming && (
          <RenameForm initial={resource} close={() => setRenaming(false)} />
        )}
      </ModalFrame>
    </>
  );
}

function RenameForm({
  initial,
  close,
}: {
  initial: SkillResource;
  close: () => void;
}) {
  const [basis, setBasis] = useState(initial),
    [name, setName] = useState(initial.value.name);
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const save = useMutation({
    mutationFn: async () => {
      if (!basis.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return client.http
        .PATCH("/api/v1/skills/{skill_id}", {
          params: {
            path: { skill_id: basis.value.id },
            header: {
              ...workspaceHeaders(basis.value.workspace_id),
              "If-Match": basis.etag,
            },
          },
          body: { name: name.trim() },
        })
        .then(data);
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["skills"] });
      close();
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      client.http
        .GET("/api/v1/skills/{skill_id}", {
          params: { path: { skill_id: basis.value.id } },
          headers: workspaceHeaders(basis.value.workspace_id),
        })
        .then(representation),
    onSuccess: (result) => {
      setBasis(result);
      setName(result.value.name);
      save.reset();
    },
  });
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField label={t("Display name")}>
        <Input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <ErrorNotice
        error={save.error ?? reload.error}
        retry={() => reload.mutate()}
      />
      <FormActions
        pending={save.isPending || reload.isPending}
        onCancel={close}
      />
    </form>
  );
}
