import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
} from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { allPages, type Schema } from "../../../shared/api";
import {
  CatalogStep,
  DirectoryEmpty,
  DirectoryList,
  DirectoryRow,
  type ResourceEditorControl,
} from "../../../shared/dialogs";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { FieldHelp } from "../../../shared/forms/field-help";
import { OptionsRefresh } from "../../../shared/forms/options-refresh";
import { environmentTemplates } from "../../environments/api";
import { AgentAvatar } from "../avatar";
import { agentRevisionsQuery } from "../queries";
import {
  suggestedSubagentName,
  subagentNameError,
  subagentLimitsError,
  type Subagent,
  type SubagentMode,
} from "../subagents";
import agentStyles from "../agents.module.css";
import styles from "./subagent-settings.module.css";
import { SubagentUsageLimits } from "./subagent-usage-limits";

export function SubagentSettings({
  initialName,
  initial,
  usedNames,
  mode,
  parentAgentId,
  agents,
  loading,
  catalogError,
  refresh,
  readOnly,
  control,
  onApply,
}: {
  initialName?: string;
  initial?: Subagent;
  usedNames: string[];
  mode: SubagentMode;
  parentAgentId?: string;
  agents: Schema["Agent"][];
  loading: boolean;
  catalogError?: unknown;
  refresh: () => Promise<unknown>;
  readOnly: boolean;
  control: ResourceEditorControl;
  onApply: (name: string, child: Subagent) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace();
  const formId = useId();
  const [child, setChild] = useState<Subagent | undefined>(initial);
  const [name, setName] = useState(initialName ?? "");
  const [query, setQuery] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<Error>();
  const [advanced, setAdvanced] = useState(readOnly);
  const selected = agents.find((item) => item.id === child?.agent_id);
  const revisions = useQuery(
    agentRevisionsQuery(client, workspace.id, child?.agent_id ?? ""),
  );
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    enabled: !!child && advanced && mode === "async",
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
  });
  const close = () => control.onClose?.();
  const nameError = subagentNameError(name, usedNames, initialName);
  function update(value: Partial<Subagent>) {
    setChild((current) => (current ? { ...current, ...value } : current));
  }
  function select(agent: Schema["Agent"]) {
    setChild({ agent_id: agent.id, revision_id: agent.default_revision_id });
    setName(suggestedSubagentName(agent.name, usedNames));
    setError(undefined);
    setAttempted(false);
  }
  function apply(event: FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    setAttempted(true);
    if (!child || readOnly || nameError) return;
    const message = subagentLimitsError(mode, child.usage_limits);
    if (message) {
      setError(new Error(t(message)));
      setAdvanced(true);
      return;
    }
    if (
      mode === "async" &&
      child.environment?.mode === "dedicated" &&
      !child.environment.template_id
    ) {
      setError(new Error(t("Choose an environment template.")));
      setAdvanced(true);
      return;
    }
    // Keep existing pins usable during catalog failures; new choices require a concrete version.
    if (!child.revision_id && !initial) {
      setError(new Error(t("Choose a published version.")));
      return;
    }
    onApply(name, child);
    close();
  }
  const versionOptions = (revisions.data ?? []).map((item) => ({
    value: item.id,
    label: `v${item.number}`,
    hint:
      item.id === selected?.default_revision_id
        ? t("Default")
        : (item.note ?? undefined),
  }));
  if (
    child?.revision_id &&
    !versionOptions.some((item) => item.value === child.revision_id)
  )
    versionOptions.push({
      value: child.revision_id,
      label: child.revision_id,
      hint: undefined,
    });
  if (child && !child.revision_id)
    versionOptions.unshift({
      value: "",
      label: t("Default version pinned on save"),
      hint: undefined,
    });
  const environment = child?.environment ?? { mode: "shared" as const };
  const templateOptions = (templates.data ?? []).map((item) => ({
    value: item.id,
    label: item.name,
  }));
  if (
    environment.template_id &&
    !templateOptions.some((item) => item.value === environment.template_id)
  )
    templateOptions.push({
      value: environment.template_id,
      label: environment.template_id,
    });
  return (
    <ModalFrame
      open={control.controlledOpen}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      finalFocus={control.finalFocus}
      size="lg"
      title={t(child ? "Configure subagent" : "Add subagent")}
      closeLabel={t("Close")}
      description={
        child
          ? (selected?.name ?? child.agent_id)
          : t(
              "Choose an agent from this workspace. Its model, instructions and tools stay with its pinned version.",
            )
      }
      footer={
        <>
          <Button type="button" variant="outline" onClick={close}>
            {t(readOnly ? "Close" : "Cancel")}
          </Button>
          {child && !readOnly && (
            <Button type="submit" form={formId}>
              {t(initial ? "Apply to draft" : "Add to draft")}
            </Button>
          )}
        </>
      }
    >
      {!child ? (
        <DirectoryList
          search={
            <div className="flex items-center gap-2">
              <Input
                type="search"
                aria-label={t("Search agents")}
                placeholder={t("Search agents…")}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <OptionsRefresh onRefresh={refresh} />
            </div>
          }
          footer={
            <a
              href={`${basePath}/agents/new`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm"
            >
              {t("Create agent")}
              <ArrowSquareOutIcon aria-hidden="true" size={14} />
              <span className="sr-only">{t("Opens in a new tab")}</span>
            </a>
          }
        >
          <ErrorNotice error={catalogError} retry={() => void refresh()} />
          {loading && <Loading />}
          {agents
            .filter(
              (agent) =>
                !agent.archived_at &&
                agent.id !== parentAgentId &&
                `${agent.name} ${agent.description}`
                  .toLowerCase()
                  .includes(query.trim().toLowerCase()),
            )
            .map((agent) => (
              <DirectoryRow
                key={agent.id}
                name={agent.name}
                detail={agent.description}
                disabled={!agent.default_revision_id}
                icon={
                  <AgentAvatar
                    name={agent.name}
                    id={agent.id}
                    url={agent.image_url}
                  />
                }
                onClick={() => select(agent)}
              />
            ))}
          {!loading &&
            !agents.some(
              (agent) =>
                !agent.archived_at &&
                agent.id !== parentAgentId &&
                `${agent.name} ${agent.description}`
                  .toLowerCase()
                  .includes(query.trim().toLowerCase()),
            ) && <DirectoryEmpty>{t("No matching agents.")}</DirectoryEmpty>}
        </DirectoryList>
      ) : (
        <CatalogStep
          backLabel={t("Back to agents")}
          onBack={!initial && !readOnly ? () => setChild(undefined) : undefined}
        >
          <form id={formId} onSubmit={apply} noValidate className={styles.form}>
            {selected?.archived_at && (
              <p className="text-muted-foreground text-xs">
                {t(
                  "This agent is archived. Its saved version is retained; choose another agent to add a new selection.",
                )}
              </p>
            )}
            <FormField
              label={t("Calling name")}
              className={styles.callingName}
              readOnly={readOnly}
              error={attempted && nameError ? t(nameError) : undefined}
              labelAction={
                <FieldHelp label={t("About calling names")}>
                  {t(
                    "The parent uses this unique name to delegate work. Changing it may affect instructions that refer to the old name.",
                  )}
                </FieldHelp>
              }
            >
              <Input
                value={name}
                maxLength={128}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <ChoiceField
              label={t("Pinned version")}
              readOnly={readOnly}
              value={child.revision_id ?? ""}
              onValueChange={(revision_id) =>
                update({ revision_id: revision_id || undefined })
              }
              options={versionOptions}
              description={t(
                "Updates to the child agent do not change this selection. Choose a newer version to adopt them.",
              )}
            />
            <ErrorNotice
              error={revisions.error}
              retry={() => void revisions.refetch()}
            />
            <TextAreaField
              label={t("Delegation description (optional)")}
              readOnly={readOnly}
              value={child.description ?? ""}
              onChange={(description) =>
                update({ description: description || null })
              }
              rows={3}
              maxLength={4096}
              placeholder={selected?.description || selected?.name}
              hint={t(
                "Tell the parent when to use this subagent. Empty uses the agent's description or name.",
              )}
            />
            <DisclosureSection
              className={`${agentStyles.outlinedDisclosure} ${styles.advanced}`}
              title={t("Advanced settings")}
              open={advanced}
              onOpenChange={setAdvanced}
            >
              <ChoiceField
                label={t("Parent task")}
                value={
                  (child.context?.include_task ?? true) ? "include" : "omit"
                }
                readOnly={readOnly}
                onValueChange={(value) =>
                  update({
                    context: {
                      ...child.context,
                      include_task: value === "include",
                    },
                  })
                }
                options={[
                  { value: "include", label: t("Include parent task") },
                  { value: "omit", label: t("Delegated task only") },
                ]}
                labelAction={
                  <FieldHelp label={t("About parent task context")}>
                    {t(
                      "The delegated task is always sent. Including the parent task also sends the parent's current text prompt when available.",
                    )}
                  </FieldHelp>
                }
              />
              <ChoiceField
                label={t("Conversation history")}
                value={child.context?.history ?? "none"}
                readOnly={readOnly}
                onValueChange={(value) =>
                  update({
                    context: {
                      ...child.context,
                      history: value as "none" | "summary" | "selected",
                    },
                  })
                }
                options={[
                  { value: "none", label: t("No history") },
                  { value: "summary", label: t("Existing summary") },
                  {
                    value: "selected",
                    label: t("Current conversation history"),
                  },
                ]}
                labelAction={
                  <FieldHelp label={t("About conversation history")}>
                    <span className="grid gap-2">
                      <span>
                        {t(
                          "No history: sends no parent conversation messages.",
                        )}
                      </span>
                      <span>
                        {t(
                          "Existing summary: sends the latest existing compaction or handoff summary. If none exists, nothing is added; no new summary is generated.",
                        )}
                      </span>
                      <span>
                        {t(
                          "Current conversation history: sends the messages currently held by the parent. History removed by compaction is not restored, and individual messages cannot be selected.",
                        )}
                      </span>
                    </span>
                  </FieldHelp>
                }
              />
              <ChoiceField
                label={t("Child environment")}
                readOnly={readOnly || mode === "inline"}
                value={
                  mode === "inline" ? "shared" : (environment.mode ?? "shared")
                }
                onValueChange={(value) =>
                  update({
                    environment: {
                      mode: value as "none" | "shared" | "dedicated",
                      ...(value === "dedicated" ? { template_id: "" } : {}),
                    },
                  })
                }
                options={[
                  { value: "shared", label: t("Share parent environment") },
                  { value: "dedicated", label: t("Dedicated environment") },
                  { value: "none", label: t("No environment") },
                ]}
                description={
                  mode === "inline"
                    ? t(
                        "Inline (sync) borrows the parent environment. Saved Async environment settings are retained for switching back.",
                      )
                    : undefined
                }
                labelAction={
                  <FieldHelp label={t("About child environments")}>
                    {t(
                      "Shared uses the parent's mounted environments. Dedicated creates an environment from the chosen template. None provides no environment. The child's own default template is not used.",
                    )}
                  </FieldHelp>
                }
              />
              {mode === "async" && environment.mode === "dedicated" && (
                <>
                  <ChoiceField
                    label={t("Environment template")}
                    value={environment.template_id ?? ""}
                    placeholder={t("Choose a template")}
                    options={templateOptions}
                    readOnly={readOnly}
                    onValueChange={(template_id) =>
                      update({
                        environment: { mode: "dedicated", template_id },
                      })
                    }
                  />
                  <ErrorNotice
                    error={templates.error}
                    retry={() => void templates.refetch()}
                  />
                </>
              )}
              <SubagentUsageLimits
                mode={mode}
                value={child.usage_limits}
                onChange={(usage_limits) => update({ usage_limits })}
                readOnly={readOnly}
              />
              {mode === "inline" && (
                <p className="text-muted-foreground text-xs">
                  {t(
                    "Inline (sync) cannot pause for user approval, user questions or client tool results.",
                  )}
                </p>
              )}
            </DisclosureSection>
            <ErrorNotice error={error} />
          </form>
        </CatalogStep>
      )}
    </ModalFrame>
  );
}
