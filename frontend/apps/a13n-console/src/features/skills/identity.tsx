import {
  Button,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  ModalFrame,
} from "a13n-ui";
import {
  DotsThreeIcon,
  PencilSimpleIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";

type SkillResource = ReturnType<typeof representation<Schema["Skill"]>>;

export function RenameSkill({ resource }: { resource: SkillResource }) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Rename skill")}
      description={t(
        "Change the display name without changing the skill key or its published versions.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Rename skill")}
          title={t("Rename skill")}
        >
          <PencilSimpleIcon size={15} />
        </Button>
      }
    >
      {open && <RenameForm initial={resource} close={() => setOpen(false)} />}
    </ModalFrame>
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
      className={styles.form}
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

export function SkillActions({ resource }: { resource: SkillResource }) {
  const client = useClient(),
    { t } = useTranslation(),
    { basePath } = useWorkspace(),
    navigate = useNavigate();
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("More skill actions")}
            title={t("More skill actions")}
          />
        }
      >
        <DotsThreeIcon size={18} />
      </MenuTrigger>
      <MenuPopup align="end">
        <Confirm
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
                t("Version information is unavailable. Reload this page."),
              );
            await client.http.DELETE("/api/v1/skills/{skill_id}", {
              params: {
                path: { skill_id: resource.value.id },
                header: {
                  ...workspaceHeaders(resource.value.workspace_id),
                  "If-Match": resource.etag,
                },
              },
            });
            navigate(`${basePath}/skills`);
          }}
        />
      </MenuPopup>
    </Menu>
  );
}
