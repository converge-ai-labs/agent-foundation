import { useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, SettingsRow, SettingsSection } from "a13n-ui";
import { Upload, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { representation, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Avatar } from "../../layout/shell";
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
      <SettingsSection>
        <SettingsRow
          label={t(target.kind === "personal" ? "Avatar" : "Icon")}
          description={t("PNG, JPEG, or WebP. Up to 5 MB.")}
        >
          <div className={styles.profileImage}>
            <Avatar name={current.value.name} url={current.value.image_url} />
            {editable && (
              <>
                <input
                  ref={uploadInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  disabled={pending}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) image.mutate(file);
                    event.target.value = "";
                  }}
                />
                <Button
                  disabled={pending}
                  icon={<Upload size={14} />}
                  onClick={() => uploadInput.current?.click()}
                >
                  {t("Upload image")}
                </Button>
                {current.value.image_url && (
                  <Button
                    aria-label={t("Remove image")}
                    variant="ghost"
                    icon={<Trash2 size={14} />}
                    disabled={pending}
                    onClick={() => image.mutate(null)}
                  />
                )}
              </>
            )}
          </div>
        </SettingsRow>
        <SettingsRow label={nameLabel} controlId={nameId}>
          <div className={styles.nameControl}>
            <Input
              id={nameId}
              label={nameLabel}
              hideLabel
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={128}
              disabled={!editable || pending}
            />
          </div>
        </SettingsRow>
        <SettingsRow label={t("ID")}>
          <code className={styles.resourceId}>{current.value.id}</code>
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice
        error={save.error ?? image.error}
        retry={() => void reload()}
      />
      {editable && name !== current.value.name && (
        <div className={styles.saveRow}>
          <Button
            disabled={pending}
            onClick={() => setName(current.value.name)}
          >
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            variant="primary"
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
