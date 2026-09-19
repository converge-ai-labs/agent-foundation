import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import {
  CheckCircleIcon,
  FileZipIcon,
  UploadSimpleIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/forms";
import { IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import shared from "../../shared/shared.module.css";
import { SourceIcon } from "./source";
import styles from "./skills.module.css";

/** Import a new skill, or publish the next version of an existing one. */
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
          {t(skill ? "New version" : "Import skill")}
        </Button>
      }
      size="lg"
      title={
        skill
          ? t("New version of {{skill}}", { skill: skill.name })
          : t("Import skill")
      }
      description={
        skill
          ? t("Publish an updated package as a new version of this skill.")
          : t("Import a skill from a ZIP file or GitHub repository.")
      }
      closeLabel={t("Close")}
      open={open}
    >
      {open && (
        <ImportForm
          skill={skill}
          onCancel={() => setOpen(false)}
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
  onCancel,
}: {
  skill?: Schema["Skill"];
  onSuccess: (skill: Schema["Skill"]) => void;
  onCancel: () => void;
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
  const busy = stage.isPending || publish.isPending;
  return (
    <form
      className={shared.form}
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
      <div className={styles.sourceField}>
        <span>{t("Source")}</span>
        <SegmentedControl
          label={t("Source")}
          value={kind}
          onValueChange={(value) => {
            setKind(value);
            publish.reset();
          }}
          options={[
            {
              value: "zip_upload",
              label: (
                <>
                  <SourceIcon kind="zip" />
                  {t("ZIP file")}
                </>
              ),
              disabled: busy,
            },
            {
              value: "github",
              label: (
                <>
                  <SourceIcon kind="github" />
                  GitHub
                </>
              ),
              disabled: busy,
            },
          ]}
        />
      </div>
      {kind === "zip_upload" ? (
        <div className={shared.stack}>
          <PackageDropZone
            label={t("Skill package")}
            file={upload?.file}
            disabled={busy}
            onSelect={(file) => {
              setUpload(file ? { file, key: crypto.randomUUID() } : undefined);
              setReceipt(undefined);
              stage.reset();
            }}
          />
          {upload && !receipt && (
            <Button
              variant="outline"
              loading={stage.isPending}
              onClick={() => stage.mutate()}
              type="button"
            >
              {t("Validate package")}
            </Button>
          )}
          {receipt && (
            <Receipt
              receipt={receipt}
              version={basis ? basis.version + 1 : 1}
            />
          )}
          <ErrorNotice error={stage.error} />
        </div>
      ) : (
        <div className={shared.stack}>
          <FormField className="min-w-0 w-full" label={t("Repository URL")}>
            <Input
              required={true}
              type="url"
              value={repository}
              onChange={(event) => setRepository(event.target.value)}
              placeholder="https://github.com/owner/repository"
            />
          </FormField>
          <DisclosureSection title={t("Advanced settings")}>
            <FormField
              className="min-w-0 w-full"
              label={t("Git ref")}
              description={t(
                "Use a branch, tag, or commit. Leave empty for the default branch.",
              )}
            >
              <Input
                value={ref}
                onChange={(event) => setRef(event.target.value)}
                placeholder={t("Default branch")}
              />
            </FormField>
            <FormField
              className="min-w-0 w-full"
              label={t("Subdirectory")}
              description={t(
                "Path to the skill inside the repository. Leave empty for the repository root.",
              )}
            >
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
          </DisclosureSection>
        </div>
      )}
      <ErrorNotice error={publish.error} />
      <FormActions
        pending={publish.isPending}
        onCancel={onCancel}
        disabled={stage.isPending || (kind === "zip_upload" && !receipt)}
        label={t(basis ? "Publish version" : "Import")}
      />
    </form>
  );
}

/** What the package turned out to contain, before anything is published. */
function Receipt({
  receipt,
  version,
}: {
  receipt: Schema["SkillUploadReceipt"];
  version: number;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.receipt} role="status">
      <div className={styles.receiptHead}>
        <IconTile size={32} tone="elevated">
          <CheckCircleIcon size={16} aria-hidden="true" />
        </IconTile>
        <strong title={receipt.manifest.skill_name}>
          {receipt.manifest.skill_name}
        </strong>
        <span className={styles.version}>v{version}</span>
      </div>
      <p className={styles.receiptFacts}>
        <span>
          {t("{{count}} files", { count: receipt.manifest.files.length })}
        </span>
        <span className={styles.dot}>·</span>
        <span>
          {t("{{size}} KB", {
            size: Math.ceil(receipt.manifest.total_size_bytes / 1024),
          })}
        </span>
        {receipt.manifest.description && (
          <>
            <span className={styles.dot}>·</span>
            <span>{receipt.manifest.description}</span>
          </>
        )}
      </p>
      <DisclosureSection title={t("Manifest")}>
        <JsonView value={receipt.manifest} />
      </DisclosureSection>
    </div>
  );
}

/**
 * The one dashed outline in the product: it says "drop a file here" and
 * nothing else does.
 */
function PackageDropZone({
  label,
  file,
  disabled,
  onSelect,
}: {
  label: string;
  file?: File;
  disabled?: boolean;
  onSelect: (file: File | undefined) => void;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  return (
    <div
      className={styles.dropZone}
      data-dragging={dragging}
      role="group"
      aria-label={label}
      onDragOver={(event) => {
        if (disabled) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        if (disabled) return;
        event.preventDefault();
        setDragging(false);
        const dropped = event.dataTransfer.files[0];
        if (dropped) onSelect(dropped);
      }}
    >
      <div className={styles.dropZoneInner}>
        <input
          ref={input}
          type="file"
          hidden
          accept=".zip,application/zip"
          disabled={disabled}
          aria-label={label}
          onChange={(event) => {
            const selected = event.target.files?.[0];
            if (selected) onSelect(selected);
            event.target.value = "";
          }}
        />
        {file ? (
          <>
            <span className={styles.dropZoneFile}>
              <FileZipIcon size={16} aria-hidden="true" />
              {file.name}
            </span>
            <div className={shared.actions}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() => input.current?.click()}
              >
                {t("Replace file")}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={disabled}
                aria-label={t("Remove file")}
                title={t("Remove file")}
                onClick={() => onSelect(undefined)}
              >
                <XIcon size={14} aria-hidden="true" />
              </Button>
            </div>
          </>
        ) : (
          <>
            <IconTile size={36} tone="elevated">
              <UploadSimpleIcon size={16} aria-hidden="true" />
            </IconTile>
            <p>{t("Drop a skill ZIP here, or choose a file.")}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => input.current?.click()}
            >
              {t("Choose file")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
