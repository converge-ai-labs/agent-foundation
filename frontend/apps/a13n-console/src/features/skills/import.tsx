import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
} from "a13n-ui";
import { FileUpload } from "../../shared/file-upload";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function ImportSkill({
  skill,
  onSuccess,
}: {
  skill?: Schema["Skill"];
  onSuccess?: (skill: Schema["Skill"]) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="default" type="button">
          {t(skill ? "Publish revision" : "Import skill")}
        </Button>
      }
      size={"md"}
      title={t(skill ? "Publish skill revision" : "Import skill")}
      description={t(
        "Import a normalized skill package from a ZIP file or GitHub repository.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && (
        <ImportForm
          skill={skill}
          onSuccess={(result) => {
            setOpen(false);
            onSuccess?.(result);
          }}
        />
      )}
    </ModalFrame>
  );
}
function ImportForm({
  skill,
  onSuccess,
}: {
  skill?: Schema["Skill"];
  onSuccess: (skill: Schema["Skill"]) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency();
  const [basis] = useState(skill),
    [kind, setKind] = useState("zip_upload"),
    [name, setName] = useState(skill?.name ?? ""),
    [repository, setRepository] = useState(""),
    [ref, setRef] = useState(""),
    [subdirectory, setSubdirectory] = useState(""),
    [commit, setCommit] = useState("");
  const [upload, setUpload] = useState<{ file: File; key: string }>(),
    [receipt, setReceipt] = useState<Schema["SkillUploadReceipt"]>();
  const stage = useMutation({
    mutationFn: async () => {
      if (!upload) throw new Error(t("Choose a ZIP file first."));
      return client.http
        .POST("/api/v1/workspaces/{workspace}/skill-uploads", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, upload.key),
          },
          headers: { "Content-Type": "application/zip" },
          body: upload.file,
        })
        .then(data);
    },
    onSuccess: setReceipt,
  });
  const publish = useMutation({
    mutationFn: async () => {
      if (kind === "zip_upload" && !receipt)
        throw new Error(t("Validate your ZIP file before publishing."));
      const source: Schema["CreateSkillRequest"]["source"] =
        kind === "zip_upload"
          ? { kind: "zip_upload", upload_id: receipt!.upload_id }
          : {
              kind: "github",
              repository_url: repository,
              ...(ref && { ref }),
              subdirectory,
              ...(commit && { expected_commit_sha: commit }),
            };
      if (basis) {
        const body = { source, expected_version: basis.version };
        return client.http
          .POST("/api/v1/skills/{skill_id}/revisions", {
            params: {
              path: { skill_id: basis.id },
              header: commandHeaders(workspace.id, key.forBody(body)),
            },
            body,
          })
          .then(data);
      }
      const body = { source, ...(name && { name }) };
      return client.http
        .POST("/api/v1/workspaces/{workspace}/skills", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["skills"] });
      onSuccess(result.skill);
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        publish.mutate();
      }}
    >
      {!basis && (
        <FormField
          className="min-w-0 w-full"
          label={t("Display name")}
          description={t("Leave empty to use the package name.")}
        >
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
      )}
      <ChoiceField
        placeholder={t("Select source")}
        value={kind}
        className="min-w-0"
        onValueChange={setKind}
        label={t("Source")}
        options={[
          { value: "zip_upload", label: t("ZIP file") },
          { value: "github", label: "GitHub" },
        ]}
      />
      {kind === "zip_upload" ? (
        <>
          <FileUpload
            label={t("ZIP file")}
            file={upload?.file}
            acceptedFileTypes={[".zip", "application/zip"]}
            onSelect={(file) => {
              setUpload(file ? { file, key: crypto.randomUUID() } : undefined);
              setReceipt(undefined);
              stage.reset();
            }}
          />
          <Button
            variant="outline"
            disabled={!upload}
            loading={stage.isPending}
            onClick={() => stage.mutate()}
            type="button"
          >
            {t("Validate package")}
          </Button>
          {receipt && (
            <DisclosureSection title={<>{t("Validated package")}</>}>
              <JsonView value={receipt.manifest} />
            </DisclosureSection>
          )}
          <ErrorNotice error={stage.error} />
        </>
      ) : (
        <>
          <FormField className="min-w-0 w-full" label={t("Repository URL")}>
            <Input
              required={true}
              type="url"
              value={repository}
              onChange={(event) => setRepository(event.target.value)}
              placeholder="https://github.com/owner/repository"
            />
          </FormField>
          <FormField className="min-w-0 w-full" label={t("Git ref")}>
            <Input
              value={ref}
              onChange={(event) => setRef(event.target.value)}
              placeholder={t("Default branch")}
            />
          </FormField>
          <FormField className="min-w-0 w-full" label={t("Subdirectory")}>
            <Input
              value={subdirectory}
              onChange={(event) => setSubdirectory(event.target.value)}
            />
          </FormField>
          <FormField
            className="min-w-0 w-full"
            label={t("Expected commit SHA (optional)")}
          >
            <Input
              value={commit}
              onChange={(event) => setCommit(event.target.value)}
            />
          </FormField>
        </>
      )}
      <ErrorNotice error={publish.error} />
      <FormActions
        pending={publish.isPending}
        label={t(basis ? "Publish revision" : "Import skill")}
      />
    </form>
  );
}
