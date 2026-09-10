import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, representation, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/image-picker";
import { AgentAvatar } from "./avatar";
import { initialConfig } from "./configuration";
import { AgentForm } from "./form";
import { changeAgentImage } from "./images";
import styles from "./agents.module.css";

export function CreateAgent() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    idempotency = useIdempotency();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string>();
  const [fileError, setFileError] = useState<Error>();
  const [created, setCreated] = useState<{
    value: Schema["Agent"];
    etag?: string;
  }>();
  useEffect(() => {
    if (!file) {
      setPreview(undefined);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const finish = (agent: Schema["Agent"]) => {
    void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
    navigate(`${basePath}/agents/${agent.key}`, { replace: true });
  };
  const upload = useMutation({
    mutationFn: async (resource: { value: Schema["Agent"]; etag?: string }) => {
      if (!file) return resource;
      if (!resource.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return changeAgentImage(
        client,
        workspace.id,
        resource.value.id,
        resource.etag,
        file,
      );
    },
    onSuccess: (result) => finish(result.value),
  });
  const create = useMutation({
    mutationFn: (body: Schema["CreateAgentRequest"]) =>
      client.http
        .POST("/api/v1/workspaces/{workspace}/agents", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, idempotency.forBody(body)),
          },
          body,
        })
        .then(representation),
    onSuccess: async (result) => {
      idempotency.reset();
      const resource = { value: result.value.agent, etag: result.etag };
      setCreated(resource);
      if (file) upload.mutate(resource);
      else finish(resource.value);
    },
  });
  const picker = (name: string) => (
    <ImagePicker
      hasImage={!!file}
      editable
      pending={create.isPending || upload.isPending}
      description={t("PNG, JPEG, or WebP. Up to 5 MB.")}
      onChange={(selected) => {
        if (selected && selected.size > MAX_IMAGE_BYTES) {
          setFileError(
            new Error(
              t("Choose a PNG, JPEG, or WebP image smaller than 5 MB."),
            ),
          );
          return;
        }
        setFileError(undefined);
        setFile(selected);
      }}
    >
      <AgentAvatar
        name={name}
        id={created?.value.id}
        url={preview}
        className="size-16 rounded-xl text-2xl"
      />
    </ImagePicker>
  );
  if (created)
    return (
      <div className={styles.editorPage}>
        <h1>{created.value.name}</h1>
        <p className="my-4 text-muted-foreground">
          {t("Agent created. Finish uploading its avatar.")}
        </p>
        {picker(created.value.name)}
        {upload.isPending && (
          <p role="status" className="mt-4">
            {t("Saving…")}
          </p>
        )}
        <ErrorNotice error={upload.error ?? fileError} />
        <div className="mt-6 flex gap-3">
          <Button
            disabled={upload.isPending}
            onClick={() => upload.mutate(created)}
          >
            {t("Retry upload")}
          </Button>
          <Button
            variant="ghost"
            disabled={upload.isPending}
            onClick={() => finish(created.value)}
          >
            {t("Continue without avatar")}
          </Button>
        </div>
      </div>
    );
  return (
    <AgentForm
      back={`${basePath}/agents`}
      initial={initialConfig("")}
      creating
      imageUrl={preview}
      imagePicker={picker}
      pending={create.isPending || upload.isPending || !!created}
      error={create.error ?? fileError}
      submit={(config, name, description) =>
        create.mutate({ config, name, description: description || null })
      }
    />
  );
}
