import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, commandHeaders, type Schema } from "../../shared/api";
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
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t(skill ? "Publish skill revision" : "Import skill")}
      description={t(
        "Import a normalized skill package from a ZIP file or GitHub repository.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button variant="primary">
          {t(skill ? "Publish revision" : "Import skill")}
        </Button>
      }
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
    </Dialog>
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
        .POST("/api/v1/workspaces/{workspace_id}/skill-uploads", {
          params: {
            path: { workspace_id: workspace.id },
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
        .POST("/api/v1/workspaces/{workspace_id}/skills", {
          params: {
            path: { workspace_id: workspace.id },
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
        <Input
          label={t("Display name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          hint={t("Leave empty to use the package name.")}
        />
      )}
      <SelectField
        placeholder={t("Select source")}
        label={t("Source")}
        value={kind}
        onValueChange={setKind}
        options={[
          { value: "zip_upload", label: t("ZIP file") },
          { value: "github", label: "GitHub" },
        ]}
      />
      {kind === "zip_upload" ? (
        <>
          <label className={styles.field}>
            {t("ZIP file")}
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => {
                const file = event.target.files?.[0];
                setUpload(
                  file ? { file, key: crypto.randomUUID() } : undefined,
                );
                setReceipt(undefined);
                stage.reset();
              }}
            />
          </label>
          <Button
            disabled={!upload}
            loading={stage.isPending}
            onClick={() => stage.mutate()}
          >
            {t("Validate package")}
          </Button>
          {receipt && (
            <details>
              <summary>{t("Validated package")}</summary>
              <JsonView value={receipt.manifest} />
            </details>
          )}
          <ErrorNotice error={stage.error} />
        </>
      ) : (
        <>
          <Input
            label={t("Repository URL")}
            type="url"
            value={repository}
            onChange={(event) => setRepository(event.target.value)}
            required
            placeholder="https://github.com/owner/repository"
          />
          <Input
            label={t("Git ref")}
            value={ref}
            onChange={(event) => setRef(event.target.value)}
            placeholder={t("Default branch")}
          />
          <Input
            label={t("Subdirectory")}
            value={subdirectory}
            onChange={(event) => setSubdirectory(event.target.value)}
          />
          <Input
            label={t("Expected commit SHA (optional)")}
            value={commit}
            onChange={(event) => setCommit(event.target.value)}
          />
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
