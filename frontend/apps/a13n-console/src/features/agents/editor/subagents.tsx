import { PlusIcon, XIcon } from "@phosphor-icons/react";
import { Badge, Button, ChoiceField } from "a13n-ui";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { allPages, data } from "../../../shared/api";
import { ListRow, ListRows, ListRowsEmpty } from "../../../shared/collection";
import { useResourceRows } from "../../../shared/dialogs";
import { ErrorNotice } from "../../../shared/feedback";
import { FieldHelp } from "../../../shared/forms/field-help";
import { Section } from "../../../shared/page";
import { AgentAvatar } from "../avatar";
import { agentRevisionsQuery } from "../queries";
import { subagentLimitsError } from "../subagents";
import type { AgentDraft } from "./draft";
import { DynamicSubagentsSettings } from "./dynamic-subagents";
import styles from "./subagents.module.css";
import { SubagentSettings } from "./subagent-settings";

export function SubagentsSection({
  draft,
  agentId,
  readOnly,
  mark,
  validation,
}: {
  draft: AgentDraft;
  agentId?: string;
  readOnly: boolean;
  mark?: ReactNode;
  validation?: unknown;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const editor = useResourceRows<{ name?: string }>();
  const sectionId = useId();
  const agents = useQuery({
    queryKey: ["subagent-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/agents", {
            params: { query: { limit: 100, cursor } },
            signal,
          })
          .then(data),
      ),
  });
  const entries = Object.entries(draft.subagents);
  const revisions = useQueries({
    queries: entries.map(([, child]) =>
      agentRevisionsQuery(client, workspace.id, child.agent_id),
    ),
  });
  useEffect(() => {
    if (validation)
      document.getElementById(sectionId)?.scrollIntoView?.({ block: "center" });
  }, [validation, sectionId]);
  const incompatible = entries.some(([, child]) =>
    subagentLimitsError(draft.subagentMode, child.usage_limits),
  );
  return (
    <>
      <Section
        id={sectionId}
        title={t("Subagents")}
        description={t(
          "Delegate to configured agents or create task-specific agents as needed.",
        )}
        actions={mark}
      >
        {(entries.length > 0 || draft.subagentMode === "inline") && (
          <div className={styles.settings}>
            <ChoiceField
              label={t("Execution mode")}
              readOnly={readOnly}
              value={draft.subagentMode}
              onValueChange={(value) =>
                draft.setSubagentMode(value as "inline" | "async")
              }
              labelAction={
                <FieldHelp label={t("About subagent execution")}>
                  {t(
                    "Applies to all subagents. Async runs children in the background: the parent continues and can inspect, steer or cancel them before receiving results. Inline (sync) waits for the child to finish within the current call.",
                  )}
                </FieldHelp>
              }
              options={[
                { value: "async", label: t("Async") },
                {
                  value: "inline",
                  label: t("Inline (sync)"),
                },
              ]}
            />
          </div>
        )}
        <DynamicSubagentsSettings draft={draft} readOnly={readOnly} />
        <div className={styles.named}>
          <div className={styles.namedHeader}>
            <div className={styles.heading}>
              <h3>{t("Named subagents")}</h3>
              {!readOnly && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={entries.length >= 128}
                  onClick={(event) => editor.activate({}, event.currentTarget)}
                >
                  <PlusIcon />
                  {t("Add subagent")}
                </Button>
              )}
            </div>
            <p className={styles.hint}>
              {t(
                "Choose existing agents that this agent can delegate tasks to by name.",
              )}
            </p>
          </div>
          {incompatible && (
            <p role="alert" className="text-destructive-foreground text-sm">
              {t(
                "Async supports only a model request limit. Clear the other limits or choose Inline (sync).",
              )}
            </p>
          )}
          <ErrorNotice error={validation} />
          <ErrorNotice
            error={agents.error}
            retry={() => void agents.refetch()}
          />
          {entries.length ? (
            <ListRows>
              {entries.map(([name, child], index) => {
                const agent = agents.data?.find(
                  (item) => item.id === child.agent_id,
                );
                const versions = revisions[index];
                const revision = versions?.data?.find(
                  (item) => item.id === child.revision_id,
                );
                const missing = agents.isSuccess && !agent;
                return (
                  <ListRow
                    key={name}
                    icon={
                      <AgentAvatar
                        name={agent?.name ?? name}
                        id={child.agent_id}
                        url={agent?.image_url}
                      />
                    }
                    name={agent?.name ?? name}
                    secondary={
                      <>
                        {name}
                        {child.revision_id
                          ? ` · ${revision ? `v${revision.number}` : child.revision_id}`
                          : ` · ${t("Default version pinned on save")}`}
                        <br />
                        {child.description ||
                          agent?.description ||
                          t("No description")}
                      </>
                    }
                    control={
                      (missing || agent?.archived_at) && (
                        <Badge variant="warning">
                          {t(missing ? "Unavailable agent" : "Archived")}
                        </Badge>
                      )
                    }
                    actions={
                      <>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          aria-label={t("Configure subagent {{name}}", {
                            name,
                          })}
                          onClick={(event) =>
                            editor.activate({ name }, event.currentTarget)
                          }
                        >
                          {t(readOnly ? "View configuration" : "Configure")}
                        </Button>
                        {!readOnly && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            aria-label={t("Remove subagent {{name}}", { name })}
                            onClick={() =>
                              draft.setSubagents((current) =>
                                Object.fromEntries(
                                  Object.entries(current).filter(
                                    ([key]) => key !== name,
                                  ),
                                ),
                              )
                            }
                          >
                            <XIcon />
                          </Button>
                        )}
                      </>
                    }
                  />
                );
              })}
            </ListRows>
          ) : (
            <ListRowsEmpty>{t("No named subagents added.")}</ListRowsEmpty>
          )}
        </div>
      </Section>
      {editor.selected && (
        <SubagentSettings
          key={`${editor.control.controlledOpen}:${editor.selected.name ?? "new"}`}
          initialName={editor.selected.name}
          initial={
            editor.selected.name
              ? draft.subagents[editor.selected.name]
              : undefined
          }
          usedNames={Object.keys(draft.subagents)}
          mode={draft.subagentMode}
          parentAgentId={agentId}
          agents={agents.data ?? []}
          loading={agents.isPending}
          catalogError={agents.error}
          refresh={() => agents.refetch({ throwOnError: true })}
          readOnly={readOnly}
          control={editor.control}
          onApply={(name, child) =>
            draft.setSubagents((current) => ({
              ...Object.fromEntries(
                Object.entries(current).filter(
                  ([key]) => key !== editor.selected?.name,
                ),
              ),
              [name]: child,
            }))
          }
        />
      )}
    </>
  );
}
