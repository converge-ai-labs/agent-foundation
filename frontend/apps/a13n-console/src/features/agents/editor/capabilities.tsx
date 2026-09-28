import { Button } from "a13n-ui";
import { PuzzlePieceIcon, XIcon } from "@phosphor-icons/react";
import { useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  ListRow,
  ListRows,
  ListRowsEmpty,
  ResourcePicker,
} from "../../../shared/collection";
import { Section } from "../../../shared/page";
import { connectionState } from "../../connections/api";
import { revisionsQuery } from "../../skills/revisions";
import type { useAgentChoices } from "../choices";
import {
  ConnectionBrandIcon,
  ConnectionGroup,
  displayName,
} from "../connections";
import agentStyles from "../agents.module.css";
import styles from "./editor.module.css";
import type { AgentDraft } from "./draft";

/** Reusable knowledge and procedures the agent can load. */
export function SkillsSection({
  draft,
  choices,
  readOnly,
}: {
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, basePath } = useWorkspace();
  const items = choices.data?.skills ?? [];
  const selected = new Set(draft.skills.map((item) => item.skill_id));
  const revisions = useQueries({
    queries: draft.skills.map((item) =>
      revisionsQuery(client, workspace.id, item.skill_id),
    ),
  });
  return (
    <Section
      title={t("Skills")}
      description={t("Reusable knowledge and procedures the agent can load.")}
      actions={
        !readOnly && (
          <ResourcePicker
            label={t("Add skill")}
            searchLabel={t("Search skills")}
            emptyLabel={t("No skills in this workspace yet.")}
            loading={choices.isPending}
            manageHref={`${basePath}/skills`}
            manageLabel={t("Manage skills")}
            // Archived skills cannot be added; a selected one can still be removed.
            items={items
              .filter((skill) => !skill.archived_at || selected.has(skill.id))
              .map((skill) => ({
                id: skill.id,
                name: skill.name,
                detail: skill.description,
              }))}
            selected={selected}
            onToggle={(id, checked) =>
              draft.setSkills((previous) =>
                checked
                  ? [...previous, { skill_id: id }]
                  : previous.filter((item) => item.skill_id !== id),
              )
            }
          />
        )
      }
    >
      {draft.skills.length ? (
        <ListRows>
          {draft.skills.map((selection, index) => {
            const skill = items.find((item) => item.id === selection.skill_id);
            const versions = revisions[index]?.data?.items ?? [];
            return (
              <ListRow
                key={selection.skill_id}
                icon={<PuzzlePieceIcon size={16} />}
                name={skill?.name ?? selection.skill_id}
                secondary={
                  skill ? skill.description : t("Not in this workspace")
                }
                control={
                  <label className={styles.rowSelect}>
                    {t("Version")}
                    <select
                      disabled={readOnly}
                      value={selection.revision_id ?? ""}
                      onChange={(event) =>
                        draft.setSkills((previous) =>
                          previous.map((item) =>
                            item.skill_id === selection.skill_id
                              ? {
                                  ...item,
                                  revision_id: event.target.value || null,
                                }
                              : item,
                          ),
                        )
                      }
                    >
                      <option value="">{t("Latest")}</option>
                      {versions.map((revision) => (
                        <option key={revision.id} value={revision.id}>
                          v{revision.number}
                        </option>
                      ))}
                      {selection.revision_id &&
                        !versions.some(
                          (revision) => revision.id === selection.revision_id,
                        ) && (
                          <option value={selection.revision_id}>
                            {selection.revision_id}
                          </option>
                        )}
                    </select>
                  </label>
                }
                actions={
                  !readOnly && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("Remove {{name}}", {
                        name: skill?.name ?? selection.skill_id,
                      })}
                      onClick={() =>
                        draft.setSkills((previous) =>
                          previous.filter(
                            (item) => item.skill_id !== selection.skill_id,
                          ),
                        )
                      }
                    >
                      <XIcon />
                    </Button>
                  )
                }
              />
            );
          })}
        </ListRows>
      ) : (
        <ListRowsEmpty>
          {t("No skills attached. The agent relies on its instructions alone.")}
        </ListRowsEmpty>
      )}
    </Section>
  );
}

/** External services and MCP servers whose tools the agent may call. */
export function ConnectionsSection({
  draft,
  choices,
  readOnly,
}: {
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const available = choices.data?.connections ?? [];
  const selected = new Set(draft.connections.map((item) => item.connection_id));
  return (
    <Section
      title={t("Connections")}
      description={t(
        "External services and MCP servers whose tools the agent may call.",
      )}
      actions={
        !readOnly && (
          <ResourcePicker
            label={t("Add connection")}
            searchLabel={t("Search connections")}
            emptyLabel={t("No connections in this workspace yet.")}
            loading={choices.isPending}
            manageHref={`${basePath}/connections`}
            manageLabel={t("Manage connections")}
            items={available.map((connection) => ({
              id: connection.id,
              name: displayName(connection),
              detail:
                "url" in connection.config
                  ? connection.config.url
                  : connection.config.app,
              icon: <ConnectionBrandIcon connection={connection} />,
              disabled: connectionState(connection) !== "ready",
              disabledReason: t(connectionState(connection)),
            }))}
            selected={selected}
            onToggle={(id, checked) =>
              draft.setConnections((previous) =>
                checked
                  ? [
                      ...previous,
                      { connection_id: id, tools: null, defer_loading: true },
                    ]
                  : previous.filter((item) => item.connection_id !== id),
              )
            }
          />
        )
      }
    >
      {draft.connections.length ? (
        <div className={agentStyles.toolsetCard}>
          {draft.connections.map((selection) => (
            <ConnectionGroup
              key={selection.connection_id}
              connection={available.find(
                (item) => item.id === selection.connection_id,
              )}
              selection={selection}
              readOnly={readOnly}
              onChange={(change) =>
                draft.setConnections((previous) =>
                  previous.map((item) =>
                    item.connection_id === selection.connection_id
                      ? change(item)
                      : item,
                  ),
                )
              }
              onRemove={() =>
                draft.setConnections((previous) =>
                  previous.filter(
                    (item) => item.connection_id !== selection.connection_id,
                  ),
                )
              }
            />
          ))}
        </div>
      ) : (
        <ListRowsEmpty>
          <span>{t("No connections attached.")}</span>
          <Link to={`${basePath}/connections`}>{t("Manage connections")}</Link>
        </ListRowsEmpty>
      )}
    </Section>
  );
}
