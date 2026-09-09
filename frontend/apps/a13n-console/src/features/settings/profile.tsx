import { Button, Input } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";

import { Camera, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { representation, type Schema } from "../../shared/api";
import { CopyableId } from "../../shared/copy";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./settings.module.css";

export type ProfileTarget =
  { kind: "personal" } | { kind: "workspace" | "organization"; id: string };
export function Profile({
  target,
  editable = true,
}: {
  target: ProfileTarget;
  editable?: boolean;
}) {
  const client = useClient();
  const [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: [
      "profile",
      target.kind,
      target.kind === "personal" ? "me" : target.id,
    ],
    queryFn: ({ signal }) => {
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
  if (query.isPending) return <Loading />;
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
  reload,
}: {
  resource: {
    value: Schema["User"] | Schema["Workspace"] | Schema["Organization"];
    etag?: string;
  };
  target: ProfileTarget;
  editable: boolean;
  reload: () => Promise<void>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [name, setName] = useState(resource.value.name),
    uploadInput = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState(resource);
  const save = useMutation({
    mutationFn: async () => {
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
      void cache.invalidateQueries();
    },
  });
  const image = useMutation({
    mutationFn: async (file: File | null) => {
      if (!current.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      if (file && file.size > 5 * 1024 * 1024)
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
  const nameLabel = t(
    target.kind === "personal"
      ? "Display name"
      : target.kind === "workspace"
        ? "Workspace name"
        : "Organization name",
  );
  return (
    <form
      className={styles.profile}
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <SettingsSection
        title={
          target.kind === "personal"
            ? undefined
            : t(target.kind === "workspace" ? "Workspace" : "Organization")
        }
      >
        <SettingsRow
          label={t(target.kind === "personal" ? "Avatar" : "Icon")}
          description={t("PNG, JPEG, or WebP. Up to 5 MB.")}
        >
          <div className={styles.profileImage}>
            {editable ? (
              <Button
                type="button"
                className={styles.imageButton}
                aria-label={t("Upload image")}
                variant="ghost"
                disabled={pending}
                onClick={() => uploadInput.current?.click()}
              >
                <UserAvatar
                  name={current.value.name}
                  url={current.value.image_url}
                />
                <span className={styles.imageOverlay} aria-hidden="true">
                  <Camera size={16} />
                </span>
              </Button>
            ) : (
              <UserAvatar
                name={current.value.name}
                url={current.value.image_url}
              />
            )}
            {editable && (
              <>
                <Input
                  ref={uploadInput}
                  type="file"
                  nativeInput
                  unstyled
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  disabled={pending}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) image.mutate(file);
                    event.target.value = "";
                  }}
                />
                {current.value.image_url && (
                  <Button
                    aria-label={t("Remove image")}
                    variant="ghost"
                    disabled={pending}
                    onClick={() => image.mutate(null)}
                    size="icon"
                    type="button"
                  >
                    {<Trash2 size={14} />}
                  </Button>
                )}
              </>
            )}
          </div>
        </SettingsRow>
        <SettingsRow label={nameLabel} controlId={nameId}>
          <div className={styles.nameControl}>
            <Input
              required
              id={nameId}
              value={name}
              disabled={!editable || pending}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
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
      {editable && name !== current.value.name && (
        <div className={styles.saveRow}>
          <Button
            variant="outline"
            disabled={pending}
            onClick={() => setName(current.value.name)}
            type="button"
          >
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            variant="default"
            disabled={pending || !name.trim()}
            loading={save.isPending}
          >
            {t("Save changes")}
          </Button>
        </div>
      )}
    </form>
  );
}
