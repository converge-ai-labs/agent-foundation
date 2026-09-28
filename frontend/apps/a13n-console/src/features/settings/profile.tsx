import { Input, SettingsRow, SettingsSection } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { useTranslation } from "react-i18next";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/forms";
import { useClient, type IdentityData } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { representation, type Schema } from "../../shared/api";
import { CopyableId } from "../../shared/identity";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { SaveBar } from "../../shared/page";
import styles from "./settings.module.css";

type ProfileRepresentation = {
  value: Schema["Profile"] | Schema["Workspace"] | Schema["Organization"];
  etag?: string;
};

export type ProfileTarget =
  { kind: "personal" } | { kind: "workspace" | "organization"; id: string };
export function Profile({
  target,
  editable = true,
  danger,
}: {
  target: ProfileTarget;
  editable?: boolean;
  /** Irreversible actions for this resource, shown in their own group. */
  danger?: ReactNode;
}) {
  const client = useClient();
  const [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: [
      "profile",
      target.kind,
      target.kind === "personal" ? "me" : target.id,
    ],
    queryFn: async ({ signal }): Promise<ProfileRepresentation> => {
      if (target.kind === "personal")
        return client.http
          .GET("/api/v1/users/me", { signal })
          .then(representation);
      if (target.kind === "workspace")
        return client.http
          .GET("/api/v1/workspaces/{workspace_id}", {
            params: { path: { workspace_id: target.id } },
            signal,
          })
          .then(representation);
      return client.http
        .GET("/api/v1/organizations/{organization_id}", {
          params: { path: { organization_id: target.id } },
          signal,
        })
        .then(representation);
    },
  });
  if (query.isPending) return <Loading variant="form" rows={3} />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <ProfileForm
      key={`${target.kind}:${query.data.value.id}:${generation}`}
      resource={query.data}
      target={target}
      editable={editable}
      danger={danger}
      reload={async () => {
        await query.refetch();
        setGeneration((value) => value + 1);
      }}
    />
  );
}
function ProfileForm({
  resource,
  target,
  editable,
  danger,
  reload,
}: {
  resource: ProfileRepresentation;
  target: ProfileTarget;
  editable: boolean;
  danger?: ReactNode;
  reload: () => Promise<void>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [name, setName] = useState(resource.value.name);
  const [current, setCurrent] = useState(resource);
  const save = useMutation({
    mutationFn: async (): Promise<ProfileRepresentation> => {
      if (!current.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const headers = { "If-Match": current.etag };
      if (target.kind === "personal")
        return client.http
          .PATCH("/api/v1/users/me", {
            params: { header: headers },
            headers,
            body: { name },
          })
          .then(representation);
      if (target.kind === "workspace")
        return client.http
          .PATCH("/api/v1/workspaces/{workspace_id}", {
            params: { header: headers, path: { workspace_id: target.id } },
            headers,
            body: { name },
          })
          .then(representation);
      return client.http
        .PATCH("/api/v1/organizations/{organization_id}", {
          params: { header: headers, path: { organization_id: target.id } },
          headers,
          body: { name },
        })
        .then(representation);
    },
    onSuccess: (result) => {
      setCurrent(result);
      // The target chose the endpoint, so it also names the returned resource.
      if (target.kind === "workspace") {
        const value = result.value as Schema["Workspace"];
        cache.setQueryData<{ items: Schema["Workspace"][] }>(
          ["workspaces", value.organization_id],
          (previous) =>
            previous && {
              ...previous,
              items: previous.items.map((item) =>
                item.id === value.id ? value : item,
              ),
            },
        );
      } else if (target.kind === "organization") {
        const value = result.value as Schema["Organization"];
        cache.setQueryData<IdentityData>(
          ["identity"],
          (previous) =>
            previous && {
              ...previous,
              organizations: previous.organizations.map((item) =>
                item.id === value.id ? value : item,
              ),
            },
        );
      }
      void cache.invalidateQueries();
    },
  });
  const image = useMutation({
    mutationFn: async (file: File | null) => {
      if (!current.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      if (file && file.size > MAX_IMAGE_BYTES)
        throw new Error(
          t("Choose a PNG, JPEG, or WebP image smaller than 5 MB."),
        );
      const headers = {
        "If-Match": current.etag,
        "Content-Type": file?.type ?? "application/octet-stream",
      };
      if (target.kind === "personal")
        return file
          ? client.http
              .PUT("/api/v1/users/me/avatar", {
                params: { header: headers },
                headers,
                body: file,
              })
              .then(representation)
          : client.http
              .DELETE("/api/v1/users/me/avatar", {
                params: { header: headers },
                headers,
              })
              .then(representation);
      if (target.kind === "workspace")
        return file
          ? client.http
              .PUT("/api/v1/workspaces/{workspace_id}/icon", {
                headers,
                params: { header: headers, path: { workspace_id: target.id } },
                body: file,
              })
              .then(representation)
          : client.http
              .DELETE("/api/v1/workspaces/{workspace_id}/icon", {
                headers,
                params: { header: headers, path: { workspace_id: target.id } },
              })
              .then(representation);
      return file
        ? client.http
            .PUT("/api/v1/organizations/{organization_id}/icon", {
              headers,
              params: { header: headers, path: { organization_id: target.id } },
              body: file,
            })
            .then(representation)
        : client.http
            .DELETE("/api/v1/organizations/{organization_id}/icon", {
              headers,
              params: { header: headers, path: { organization_id: target.id } },
            })
            .then(representation);
    },
    onSuccess: (result) => {
      setCurrent(result);
      void cache.invalidateQueries();
    },
  });
  const nameId = useId();
  const pending = save.isPending || image.isPending;
  const dirty = name !== current.value.name;
  const nameLabel = t(
    target.kind === "personal"
      ? "Display name"
      : target.kind === "workspace"
        ? "Workspace name"
        : "Organization name",
  );
  return (
    <form
      className={styles.sections}
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <SettingsSection
        title={
          target.kind === "personal"
            ? t("Account")
            : t(target.kind === "workspace" ? "Workspace" : "Organization")
        }
      >
        <SettingsRow
          label={t(target.kind === "personal" ? "Avatar" : "Icon")}
          description={t("PNG, JPEG, or WebP. Up to 5 MB.")}
        >
          <ImagePicker
            hasImage={!!current.value.image_url}
            editable={editable}
            pending={pending}
            onChange={(file) => image.mutate(file)}
          >
            <UserAvatar
              name={current.value.name}
              url={current.value.image_url}
              className="size-12 rounded-xl"
            />
          </ImagePicker>
        </SettingsRow>
        <SettingsRow
          label={nameLabel}
          controlId={editable ? nameId : undefined}
        >
          <div className={styles.nameControl}>
            {editable ? (
              <Input
                required
                id={nameId}
                value={name}
                disabled={pending}
                onChange={(event) => setName(event.target.value)}
                maxLength={128}
              />
            ) : (
              <span className="select-text text-sm">{name}</span>
            )}
          </div>
        </SettingsRow>
        <SettingsRow label={t("ID")}>
          <CopyableId value={current.value.id} />
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice
        error={save.error ?? image.error}
        retry={() => void reload()}
      />
      {danger && (
        <SettingsSection
          title={t("Danger zone")}
          description={t("These actions cannot be undone.")}
        >
          <SettingsRow
            label={t("Archive this workspace")}
            description={t(
              "Everything in this workspace stays readable but can no longer change or run.",
            )}
          >
            {danger}
          </SettingsRow>
        </SettingsSection>
      )}
      {editable && dirty && (
        <SaveBar
          title={t("Unsaved changes")}
          consequence={t("Saving updates the name everyone sees.")}
          onDiscard={() => setName(current.value.name)}
          saveLabel={t("Save changes")}
          pending={save.isPending}
          disabled={pending || !name.trim()}
        />
      )}
    </form>
  );
}
