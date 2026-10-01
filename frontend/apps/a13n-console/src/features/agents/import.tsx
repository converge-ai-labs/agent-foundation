import {
  CaretDownIcon,
  FileArrowUpIcon,
  PlusIcon,
  RocketLaunchIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  FormField,
  Input,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  SearchPicker,
  DisclosureSection,
} from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice, ErrorToast } from "../../shared/feedback";
import { FileUpload } from "../../shared/forms";
import { FormActions, TextAreaField } from "../../shared/forms";
import shared from "../../shared/shared.module.css";
import styles from "./agents.module.css";
import { useAgentComposer } from "./composer";
import { AgentFilePreview } from "./export";
import {
  agentDependencies,
  inspectAgentDependencies,
} from "./transfer-dependencies";
import {
  MAX_AGENT_FILE_BYTES,
  parseAgentFile,
  serializeAgentFile,
  validAgentName,
  type AgentFile,
} from "./transfer";

export function AgentCreationMenu() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const composer = useAgentComposer();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <div className={styles.creationActions}>
        {composer.available && (
          <Button
            variant="default"
            loading={composer.pending}
            onClick={() => composer.start()}
          >
            <RocketLaunchIcon size={16} aria-hidden="true" />
            {t("Create with AI")}
          </Button>
        )}
        <div className={styles.splitButton}>
          <Button variant="outline" onClick={() => navigate("new")}>
            <PlusIcon size={15} aria-hidden="true" />
            {t("Create manually")}
          </Button>
          <Menu>
            <MenuTrigger
              render={
                <Button
                  ref={triggerRef}
                  variant="outline"
                  size="icon"
                  aria-label={t("More ways to create an agent")}
                  title={t("More ways to create an agent")}
                />
              }
            >
              <CaretDownIcon size={14} aria-hidden="true" />
            </MenuTrigger>
            <MenuPopup align="end" className="min-w-48">
              <MenuItem onClick={() => setOpen(true)}>
                <FileArrowUpIcon size={16} aria-hidden="true" />
                {t("Import YAML")}
              </MenuItem>
            </MenuPopup>
          </Menu>
        </div>
      </div>
      <ModalFrame
        open={open}
        onOpenChange={(value) => {
          if (!busy) setOpen(value);
        }}
        finalFocus={triggerRef}
        title={t("Import agent")}
        description={t(
          "Upload or paste an Agent YAML file, review its dependencies, and create a new agent.",
        )}
        closeLabel={t("Close")}
        size="lg"
      >
        {open && (
          <ImportAgentForm
            onCancel={() => setOpen(false)}
            onBusyChange={setBusy}
            onSuccess={() => setOpen(false)}
          />
        )}
      </ModalFrame>
      <ErrorToast error={composer.error} />
    </>
  );
}

export function ImportAgentForm({
  onCancel,
  onSuccess,
  onBusyChange,
}: {
  onCancel: () => void;
  onSuccess: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const client = useClient(),
    { workspace, basePath } = useWorkspace();
  const cache = useQueryClient(),
    navigate = useNavigate();
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File>();
  const [reading, setReading] = useState(false);
  const readGeneration = useRef(0);
  const [draft, setDraft] = useState<AgentFile>();
  const [error, setError] = useState<Error>();
  const references = draft ? agentDependencies(draft.config) : [];
  const dependencies = useQuery({
    queryKey: [
      "agent-import-dependencies",
      workspace.id,
      references.map(({ path, kind, value, revision }) => [
        path,
        kind,
        value,
        revision,
      ]),
    ],
    enabled: !!draft,
    gcTime: 0,
    retry: false,
    queryFn: ({ signal }) =>
      inspectAgentDependencies(client, workspace.id, draft!.config, signal),
  });
  const create = useMutation({
    mutationFn: (file: AgentFile) =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/agents", {
          body: {
            name: file.name,
            description: file.description ?? "",
            config: file.config,
          },
        })
        .then(data),
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      onSuccess();
      navigate(`${basePath}/agents/${result.id}`);
    },
    onSettled: () => onBusyChange?.(false),
  });
  const unresolved =
    !dependencies.data || dependencies.data.some((item) => !item.available);
  const blocked =
    create.isPending ||
    dependencies.isFetching ||
    !!dependencies.error ||
    unresolved;
  async function selectFile(selected: File | undefined) {
    const generation = ++readGeneration.current;
    setFile(selected);
    setError(undefined);
    if (!selected) {
      setSource("");
      setReading(false);
      return;
    }
    if (selected.size > MAX_AGENT_FILE_BYTES) {
      setSource("");
      setReading(false);
      setError(new Error(t("Choose an Agent YAML file no larger than 1 MB.")));
      return;
    }
    setReading(true);
    try {
      const text = await selected.text();
      if (readGeneration.current === generation) setSource(text);
    } catch {
      if (readGeneration.current === generation) {
        setSource("");
        setError(
          new Error(t("Could not read the Agent file. Choose it again.")),
        );
      }
    } finally {
      if (readGeneration.current === generation) setReading(false);
    }
  }
  function review() {
    try {
      setDraft(parseAgentFile(source));
      setError(undefined);
    } catch (error) {
      setError(
        new Error(
          t(error instanceof Error ? error.message : "Invalid configuration"),
        ),
      );
    }
  }
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (!draft) {
          if (!reading) review();
          return;
        }
        if (blocked || !validAgentName(draft.name)) return;
        try {
          // Remapped references and edited metadata must satisfy the same file contract.
          const checked = parseAgentFile(serializeAgentFile(draft));
          setError(undefined);
          onBusyChange?.(true);
          create.mutate(checked);
        } catch (error) {
          setError(
            new Error(
              t(
                error instanceof Error
                  ? error.message
                  : "Invalid configuration",
              ),
            ),
          );
        }
      }}
    >
      {!draft ? (
        <>
          <FileUpload
            label={t("Agent YAML file")}
            file={file}
            acceptedFileTypes={[
              ".yaml",
              ".yml",
              "application/yaml",
              "text/yaml",
            ]}
            onSelect={(selected) => void selectFile(selected)}
          />
          <TextAreaField
            label={t("Agent YAML")}
            code
            value={source}
            rows={12}
            hint={t("Paste YAML or choose a file up to 1 MB.")}
            onChange={(text) => {
              ++readGeneration.current;
              setReading(false);
              setSource(text);
              setFile(undefined);
              setError(undefined);
            }}
          />
          {reading && (
            <p role="status" className={styles.importStatus}>
              {t("Reading file…")}
            </p>
          )}
          <ErrorNotice error={error} />
          <FormActions
            label={t("Review")}
            pending={reading}
            disabled={!source.trim()}
            onCancel={onCancel}
          />
        </>
      ) : (
        <>
          <fieldset
            disabled={create.isPending}
            className={`fieldset-reset ${styles.importReview}`}
          >
            <FormField label={t("Agent name")}>
              <Input
                required
                value={draft.name}
                onChange={(event) => {
                  setDraft({ ...draft, name: event.target.value });
                  create.reset();
                }}
              />
            </FormField>
            <TextAreaField
              label={t("Description")}
              value={draft.description ?? ""}
              rows={2}
              onChange={(description) => {
                setDraft({ ...draft, description });
                create.reset();
              }}
            />
            <section
              className={styles.dependencies}
              aria-label={t("Dependencies")}
            >
              <div className={styles.dependenciesIntro}>
                <h3>{t("Dependencies")}</h3>
                <p>
                  {t(
                    "Match each reference to a resource in this workspace. Pinned versions and settings are preserved.",
                  )}
                </p>
              </div>
              {dependencies.isFetching && (
                <p role="status" className={styles.importStatus}>
                  {t("Checking dependencies…")}
                </p>
              )}
              {references.map((ref) => {
                const check = dependencies.data?.find(
                  (item) => item.path === ref.path,
                );
                return (
                  <div key={ref.path} className={styles.dependency}>
                    <div className={styles.dependencyHead}>
                      <span title={ref.path}>{ref.path}</span>
                      {check?.version != null && (
                        <span>
                          {t("Pinned version {{version}}", {
                            version: check.version,
                          })}
                        </span>
                      )}
                    </div>
                    <SearchPicker
                      label={ref.path}
                      placeholder={ref.value}
                      emptyMessage={t("No available resources")}
                      value={ref.value}
                      disabled={create.isPending || dependencies.isFetching}
                      groups={[
                        {
                          label: t("Available resources"),
                          options: check?.options ?? [],
                        },
                      ]}
                      onValueChange={(value) => {
                        setDraft({ ...draft, config: ref.replace(value) });
                        create.reset();
                        setError(undefined);
                      }}
                    />
                    {check?.issue && (
                      <p role="alert" className={styles.dependencyIssue}>
                        {t(check.issue)}
                      </p>
                    )}
                  </div>
                );
              })}
              <ErrorNotice
                error={dependencies.error}
                retry={() => void dependencies.refetch()}
              />
            </section>
            <DisclosureSection title={t("Configuration preview")}>
              <AgentFilePreview file={draft} />
            </DisclosureSection>
            <p className={styles.importNote}>
              {t(
                "Creates a new agent. Service validates the configuration on creation; plugin availability also depends on the execution environment.",
              )}
            </p>
          </fieldset>
          <ErrorNotice error={error ?? create.error} />
          <div data-a13n-form-actions className={styles.importActions}>
            <Button
              variant="ghost"
              disabled={create.isPending}
              onClick={() => {
                setSource(serializeAgentFile(draft));
                setDraft(undefined);
                setFile(undefined);
                setError(undefined);
                create.reset();
              }}
            >
              {t("Back")}
            </Button>
            <div className={styles.importActionsEnd}>
              <Button
                variant="ghost"
                disabled={create.isPending}
                onClick={onCancel}
              >
                {t("Cancel")}
              </Button>
              <Button
                type="submit"
                loading={create.isPending}
                disabled={blocked || !validAgentName(draft.name)}
              >
                {t("Create agent")}
              </Button>
            </div>
          </div>
        </>
      )}
    </form>
  );
}
