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
  ArchiveIcon,
  DotsThreeOutlineVerticalIcon,
  DownloadSimpleIcon,
  PencilSimpleIcon,
} from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  data,
  ifMatch,
  representation,
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
  revision,
}: {
  resource: SkillResource;
  revisionId: string;
  /** Unknown until the viewed revision loads; the download is named after it. */
  revision?: Schema["SkillRevision"];
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    { can } = useWorkspace();
  const skill = resource.value;
  const [renaming, setRenaming] = useState(false);
  const download = useArchiveDownload(
    { id: revisionId, skill_id: skill.id, workspace_id: skill.workspace_id },
    `${revision?.config.name}-v${revision?.number}.zip`,
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
          {can("write") && (
            <MenuItem closeOnClick={true} onClick={() => setRenaming(true)}>
              <PencilSimpleIcon size={14} />
              {t("Rename skill")}
            </MenuItem>
          )}
          <MenuItem disabled={!revision} onClick={() => download.mutate()}>
            <DownloadSimpleIcon size={14} />
            {t("Download ZIP")}
          </MenuItem>
          {can("write") && (
            <>
              <MenuSeparator />
              <Confirm
                subject={skill.name}
                title={t(
                  skill.archived_at ? "Unarchive skill" : "Archive skill",
                )}
                description={t(
                  "Archived skills keep their versions for agents that already use them, but cannot publish new versions or be added to agents.",
                )}
                danger={!skill.archived_at}
                triggerElement={
                  <MenuItem
                    closeOnClick={false}
                    variant={skill.archived_at ? "default" : "destructive"}
                  >
                    <ArchiveIcon size={14} />
                    {t(skill.archived_at ? "Unarchive skill" : "Archive skill")}
                  </MenuItem>
                }
                action={async () => {
                  if (!resource.etag)
                    throw new Error(
                      t(
                        "Version information is unavailable. Reload this page.",
                      ),
                    );
                  await client
                    .workspace(skill.workspace_id)
                    .POST(
                      skill.archived_at
                        ? "/api/v1/skills/{skill_id}/unarchive"
                        : "/api/v1/skills/{skill_id}/archive",
                      {
                        params: {
                          path: {
                            skill_id: skill.id,
                          },
                        },
                        headers: ifMatch(resource.etag),
                      },
                    )
                    .then(data);
                  await cache.invalidateQueries({ queryKey: ["skills"] });
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
          "Change the display name without changing its published versions or the SKILL.md name agents see.",
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
      return client
        .workspace(basis.value.workspace_id)
        .PATCH("/api/v1/skills/{skill_id}", {
          params: {
            path: {
              skill_id: basis.value.id,
            },
          },
          headers: ifMatch(basis.etag),
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
      client
        .workspace(basis.value.workspace_id)
        .GET("/api/v1/skills/{skill_id}", {
          params: {
            path: {
              skill_id: basis.value.id,
            },
          },
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
