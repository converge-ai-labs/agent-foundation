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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  data,
  ifMatch,
  rowTag,
  uploadFile,
  type Schema,
} from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/forms";
import { IconTile, ResourceEditorButton } from "../../shared/identity";
import shared from "../../shared/shared.module.css";
import { revisionsQuery } from "./revisions";
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
        <ResourceEditorButton
          createLabel={skill ? "New version" : "Import skill"}
        />
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
    { t } = useTranslation();
  const [basis] = useState(skill),
    [kind, setKind] = useState("zip_upload"),
    [name, setName] = useState(skill?.name ?? ""),
    [repository, setRepository] = useState(""),
    [ref, setRef] = useState(""),
    [subdirectory, setSubdirectory] = useState(""),
    [commit, setCommit] = useState("");
  const [upload, setUpload] = useState<{ file: File; key: string }>(),
    [receipt, setReceipt] = useState<Schema["SkillManifest"]>();
  // A new version follows the latest one, which leads the first revision page.
  const latest = useQuery({
    ...revisionsQuery(client, workspace.id, basis?.id ?? ""),
    enabled: !!basis,
  }).data?.items[0]?.number;
  const version = !basis ? 1 : latest === undefined ? undefined : latest + 1;
  const stage = useMutation({
    mutationFn: async () => {
      if (!upload) throw new Error(t("Choose a ZIP file first."));
      const staged = await uploadFile(
        client,
        workspace.id,
        upload.file,
        upload.key,
      );
      // The Service checks the package exactly as publishing will, storing nothing.
      return client
        .workspace(workspace.id)
        .POST("/api/v1/skills/validate", {
          body: { source: { kind: "upload", upload_id: staged.upload_id } },
        })
        .then(data);
    },
    onSuccess: setReceipt,
  });
  const publish = useMutation({
    mutationFn: async (): Promise<Schema["Skill"]> => {
      const source: Schema["SkillCreate"]["source"] | undefined =
        kind === "zip_upload"
          ? receipt?.source
          : {
              kind: "github",
              repository: githubRepository(repository),
              ...(ref && { ref }),
              path: subdirectory,
              ...(commit && { commit }),
            };
      if (!source)
        throw new Error(t("Validate your ZIP file before publishing."));
      if (basis) {
        data(
          await client
            .workspace(workspace.id)
            .POST("/api/v1/skills/{skill_id}/revisions", {
              params: {
                path: { skill_id: basis.id },
              },
              headers: ifMatch(rowTag(basis)),
              body: { source },
            }),
        );
        return basis;
      }
      return client
        .workspace(workspace.id)
        .POST("/api/v1/skills", {
          body: { source, ...(name && { name }) },
        })
        .then(data);
    },
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["skills"] });
      onSuccess(result);
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
          {receipt && <Receipt receipt={receipt} version={version} />}
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
  receipt: Schema["SkillManifest"];
  version?: number;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.receipt} role="status">
      <div className={styles.receiptHead}>
        <IconTile size={32} tone="elevated">
          <CheckCircleIcon size={16} aria-hidden="true" />
        </IconTile>
        <strong title={receipt.name}>{receipt.name}</strong>
        {version !== undefined && (
          <span className={styles.version}>v{version}</span>
        )}
      </div>
      <p className={styles.receiptFacts}>
        <span>{t("{{count}} files", { count: receipt.files.length })}</span>
        <span className={styles.dot}>·</span>
        <span>
          {t("{{size}} KB", { size: Math.ceil(receipt.size / 1024) })}
        </span>
        {receipt.description && (
          <>
            <span className={styles.dot}>·</span>
            <span>{receipt.description}</span>
          </>
        )}
      </p>
      <DisclosureSection title={t("Manifest")}>
        <JsonView value={receipt} />
      </DisclosureSection>
    </div>
  );
}

/** GitHub sources name `owner/repository`; the form asks for the repository URL. */
function githubRepository(url: string) {
  const match = /^https:\/\/github\.com\/([^/]+\/[^/]+?)(?:\.git)?\/?$/.exec(
    url.trim(),
  );
  // Anything else goes to the Service as typed, which reports the invalid field.
  return match?.[1] ?? url;
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
