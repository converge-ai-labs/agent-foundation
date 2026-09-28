import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { representation, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { ImagePicker, MAX_IMAGE_BYTES } from "../../shared/forms";
import { DetailHeader, DetailPage, Section } from "../../shared/page";
import { AgentAvatar } from "./avatar";
import { initialConfig, type AgentConfig } from "./configuration";
import { AgentEditor } from "./editor";
import editorStyles from "./editor/editor.module.css";
import { changeAgentImage } from "./images";

/** Creation reuses the configuration editor with no published version yet. */
export function CreateAgent() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
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
    navigate(`${basePath}/agents/${agent.id}`, { replace: true });
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
    mutationFn: (config: AgentConfig) =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/agents", {
          body: { name, description, config },
        })
        .then(representation),
    onSuccess: (resource) => {
      setCreated(resource);
      if (file) upload.mutate(resource);
      else finish(resource.value);
    },
  });
  const picker = (label: string) => (
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
        name={label}
        id={created?.value.id}
        url={preview}
        className="size-16 rounded-xl text-2xl"
      />
    </ImagePicker>
  );
  if (created)
    return (
      <DetailPage
        back={`${basePath}/agents`}
        backLabel={t("Agents")}
        header={
          <DetailHeader
            name={created.value.name}
            description={t("Agent created. Finish uploading its avatar.")}
          />
        }
      >
        <Section title={t("Avatar")}>
          {picker(created.value.name)}
          {upload.isPending && <p role="status">{t("Saving…")}</p>}
          <ErrorNotice error={upload.error ?? fileError} />
          <div className="flex gap-3">
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
        </Section>
      </DetailPage>
    );
  return (
    <DetailPage
      back={`${basePath}/agents`}
      backLabel={t("Agents")}
      header={
        <DetailHeader
          name={t("Create agent")}
          description={t("Start with clear instructions and the right model.")}
        />
      }
    >
      <AgentEditor
        initial={initialConfig()}
        pending={create.isPending || upload.isPending || !!created}
        error={create.error ?? fileError}
        saveLabel={t("Create agent")}
        saveDisabled={!name.trim()}
        discard={() => navigate(`${basePath}/agents`)}
        identity={
          <Section
            title={t("Identity")}
            description={t("How this agent appears across the console.")}
          >
            <div className={editorStyles.identityGrid}>
              {picker(name)}
              <div className={editorStyles.identityFields}>
                <FormField label={t("Agent name")}>
                  <Input
                    required
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    maxLength={128}
                  />
                </FormField>
                <FormField label={t("Description")}>
                  <Input
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    maxLength={4096}
                  />
                </FormField>
              </div>
            </div>
          </Section>
        }
        submit={(config) => create.mutate(config)}
      />
    </DetailPage>
  );
}
