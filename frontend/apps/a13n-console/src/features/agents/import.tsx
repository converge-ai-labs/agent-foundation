import {
  CaretDownIcon,
  FileArrowUpIcon,
  PlusIcon,
  SparkleIcon,
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
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FileUpload } from "../../shared/file-upload";
import { FormActions, TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
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
  const { basePath } = useWorkspace();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Menu>
        <MenuTrigger render={<Button ref={triggerRef} variant="default" />}>
          <PlusIcon size={15} />
          {t("Create agent")}
          <CaretDownIcon size={14} />
        </MenuTrigger>
        <MenuPopup align="end" className="min-w-48">
          <MenuItem onClick={() => navigate(`${basePath}/configuration/new`)}>
            <SparkleIcon size={16} aria-hidden="true" />
            {t("Configure with assistant")}
          </MenuItem>
          <MenuItem onClick={() => navigate("new")}>
            <PlusIcon size={16} />
            {t("New agent")}
          </MenuItem>
          <MenuItem onClick={() => setOpen(true)}>
            <FileArrowUpIcon size={16} />
            {t("Import from YAML")}
          </MenuItem>
        </MenuPopup>
      </Menu>
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
    navigate = useNavigate(),
    idempotency = useIdempotency();
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
      references.map(({ path, kind, value, version }) => [
        path,
        kind,
        value,
        version,
      ]),
    ],
    enabled: !!draft,
    gcTime: 0,
    retry: false,
    queryFn: ({ signal }) =>
      inspectAgentDependencies(client, workspace.id, draft!.config, signal),
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
        .then(data),
    onSuccess: (result) => {
      idempotency.reset();
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      onSuccess();
      navigate(`${basePath}/agents/${result.agent.key}`);
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
      className={styles.form}
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
          create.mutate({
            name: checked.name,
            description: checked.description,
            config: checked.config,
          });
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
          {reading && <p role="status">{t("Reading file…")}</p>}
          <ErrorNotice error={error} />
          <FormActions
            label={t("Review import")}
            pending={reading}
            disabled={!source.trim()}
            onCancel={onCancel}
          />
        </>
      ) : (
        <>
          <fieldset
            disabled={create.isPending}
            className="fieldset-reset flex flex-col gap-4"
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
            <section className="space-y-4" aria-label={t("Dependencies")}>
              <div>
                <h3 className="text-sm font-semibold">{t("Dependencies")}</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t(
                    "Match each reference to a resource in this workspace. Pinned versions and settings are preserved.",
                  )}
                </p>
              </div>
              {dependencies.isFetching && (
                <p role="status" className="text-sm text-muted-foreground">
                  {t("Checking dependencies…")}
                </p>
              )}
              {references.map((ref) => {
                const check = dependencies.data?.find(
                  (item) => item.path === ref.path,
                );
                return (
                  <div key={ref.path} className="space-y-1.5">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="break-all text-xs text-muted-foreground">
                        {ref.path}
                      </span>
                      {ref.version != null && (
                        <span className="text-xs text-muted-foreground">
                          {t("Pinned version {{version}}", {
                            version: ref.version,
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
                      <p role="alert" className="text-xs text-destructive">
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
            <p className="text-xs text-muted-foreground">
              {t(
                "Creates a new agent. Service validates the configuration on creation; plugin availability and secret access also depend on the execution environment.",
              )}
            </p>
          </fieldset>
          <ErrorNotice error={error ?? create.error} />
          <div
            data-a13n-form-actions
            className="flex flex-wrap justify-between gap-3"
          >
            <Button
              variant="outline"
              disabled={create.isPending}
              onClick={() => {
                setSource(serializeAgentFile(draft));
                setDraft(undefined);
                setFile(undefined);
                setError(undefined);
                create.reset();
              }}
            >
              {t("Edit YAML")}
            </Button>
            <div className="flex gap-3">
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
