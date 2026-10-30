import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { representation, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { ImagePicker } from "../../shared/forms";
import { DetailHeader, DetailPage, Section } from "../../shared/page";
import { AgentAvatar } from "./avatar";
import { initialConfig, type AgentConfig } from "./configuration";
import { AgentEditor } from "./editor";
import { changeAgentImage } from "./images";
import styles from "./agents.module.css";

/** Creation reuses the configuration editor with no published version yet. */
export function CreateAgent() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const formId = useId();
  const nameInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [nameMissing, setNameMissing] = useState(false);
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string>();
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
      description={t(
        "PNG, JPEG or WebP, up to 20 MiB. Crop and compress before saving.",
      )}
      onChange={setFile}
    >
      <AgentAvatar
        name={label}
        id={created?.value.id ?? "new-agent"}
        url={preview}
        className="size-14 rounded-xl text-xl"
      />
    </ImagePicker>
  );
  const pending = create.isPending || upload.isPending || !!created;
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
          <ErrorNotice error={upload.error} />
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
      className={styles.createPage}
      header={
        // The page's actions stay in reach while the form scrolls beneath.
        <header className={styles.createBar}>
          <h1>{t("Create agent")}</h1>
          <div className={styles.createActions}>
            <Button
              variant="ghost"
              disabled={pending}
              onClick={() => navigate(`${basePath}/agents`)}
            >
              {t("Cancel")}
            </Button>
            <Button type="submit" form={formId} loading={pending}>
              {t("Create agent")}
            </Button>
          </div>
        </header>
      }
    >
      <AgentEditor
        id={formId}
        initial={initialConfig()}
        pending={pending}
        error={create.error}
        unnamed={!name.trim()}
        onUnnamed={() => {
          setNameMissing(true);
          nameInput.current?.focus();
        }}
        profile={
          <Section
            title={t("Basic info")}
            description={t("How the agent appears in lists and sessions.")}
            className={styles.profile}
          >
            <FormField
              label={t("Name")}
              error={nameMissing && !name.trim() && t("Name the agent.")}
            >
              <Input
                ref={nameInput}
                required
                autoFocus
                placeholder={t("For example, Release reviewer")}
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={128}
              />
            </FormField>
            <FormField
              label={t("Description (optional)")}
              description={t("One line shown under the name.")}
            >
              <Input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={4096}
              />
            </FormField>
            <div className={styles.profileAvatar}>
              <span>{t("Avatar")}</span>
              {picker(name)}
            </div>
          </Section>
        }
        submit={(config) => create.mutate(config)}
      />
    </DetailPage>
  );
}
