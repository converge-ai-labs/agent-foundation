import { Button, Input } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/forms";
import { ResourceKeyField } from "../../shared/identity";
import { useClient, type IdentityData } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { representation, type Schema } from "../../shared/api";
import { CopyableId } from "../../shared/identity";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./settings.module.css";

type ProfileRepresentation = {
  value: Schema["User"] | Schema["Workspace"] | Schema["Organization"];
  etag?: string;
};

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
    queryFn: async ({ signal }): Promise<ProfileRepresentation> => {
      if (target.kind === "personal")
        return client.http
          .GET("/api/v1/users/me", { signal })
          .then(representation);
      if (target.kind === "workspace")
        return client.http
          .GET("/api/v1/workspaces/{workspace}", {
            params: { path: { workspace: target.id } },
            signal,
          })
          .then(representation);
      return client.http
        .GET("/api/v1/organizations/{organization}", {
          params: { path: { organization: target.id } },
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
  resource: ProfileRepresentation;
  target: ProfileTarget;
  editable: boolean;
  reload: () => Promise<void>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    location = useLocation();
  const [name, setName] = useState(resource.value.name);
  const [key, setKey] = useState(
    "key" in resource.value ? resource.value.key : "",
  );
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
          .PATCH("/api/v1/workspaces/{workspace}", {
            params: { header: headers, path: { workspace: target.id } },
            headers,
            body: { name, key },
          })
          .then(representation);
      return client.http
        .PATCH("/api/v1/organizations/{organization}", {
          params: { header: headers, path: { organization: target.id } },
          headers,
          body: { name, key },
        })
        .then(representation);
    },
    onSuccess: (result) => {
      setCurrent(result);
      const value = result.value;
      if ("organization_id" in value) {
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
      } else if ("key" in value) {
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
      const oldKey = "key" in current.value ? current.value.key : undefined;
      if (
        target.kind === "workspace" &&
        "key" in result.value &&
        oldKey !== result.value.key
      ) {
        const parts = location.pathname.split("/");
        parts[2] = result.value.key;
        navigate(parts.join("/") + location.search, { replace: true });
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
              .PUT("/api/v1/workspaces/{workspace}/icon", {
                headers,
                params: { header: headers, path: { workspace: target.id } },
                body: file,
              })
              .then(representation)
          : client.http
              .DELETE("/api/v1/workspaces/{workspace}/icon", {
                headers,
                params: { header: headers, path: { workspace: target.id } },
              })
              .then(representation);
      return file
        ? client.http
            .PUT("/api/v1/organizations/{organization}/icon", {
              headers,
              params: { header: headers, path: { organization: target.id } },
              body: file,
            })
            .then(representation)
        : client.http
            .DELETE("/api/v1/organizations/{organization}/icon", {
              headers,
              params: { header: headers, path: { organization: target.id } },
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
        {"key" in current.value && (
          <div className={styles.profileKey}>
            <ResourceKeyField
              value={key}
              onChange={setKey}
              disabled={pending}
              readOnly={!editable}
            />
          </div>
        )}
        <SettingsRow label={t("ID")}>
          <CopyableId value={current.value.id} />
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice
        error={save.error ?? image.error}
        retry={() => void reload()}
      />
      {editable &&
        (name !== current.value.name ||
          ("key" in current.value && key !== current.value.key)) && (
          <div className={styles.saveRow}>
            <Button
              variant="outline"
              disabled={pending}
              onClick={() => {
                setName(current.value.name);
                if ("key" in current.value) setKey(current.value.key);
              }}
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
