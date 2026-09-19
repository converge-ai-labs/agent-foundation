import { Button } from "a13n-ui";
import { PuzzlePieceIcon, XIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useWorkspace } from "../../../layout/workspace";
import {
  ListRow,
  ListRows,
  ListRowsEmpty,
  ResourcePicker,
} from "../../../shared/collection";
import { Section } from "../../../shared/page";
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
  const { basePath } = useWorkspace();
  const items = choices.data?.skills ?? [];
  const selected = new Set(draft.skills.map((item) => item.skill_key));
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
            items={items.map((skill) => ({
              id: skill.key,
              name: skill.name,
              detail: skill.key,
            }))}
            selected={selected}
            onToggle={(key, checked) =>
              draft.setSkills((previous) =>
                checked
                  ? [...previous, { skill_key: key }]
                  : previous.filter((item) => item.skill_key !== key),
              )
            }
          />
        )
      }
    >
      {draft.skills.length ? (
        <ListRows>
          {draft.skills.map((selection) => {
            const skill = items.find(
              (item) => item.key === selection.skill_key,
            );
            const versions = Array.from(
              { length: skill?.version ?? 0 },
              (_, index) => skill!.version - index,
            );
            return (
              <ListRow
                key={selection.skill_key}
                icon={<PuzzlePieceIcon size={16} />}
                name={skill?.name ?? selection.skill_key}
                secondary={skill ? skill.key : t("Not in this workspace")}
                control={
                  <label className={styles.rowSelect}>
                    {t("Version")}
                    <select
                      disabled={readOnly}
                      value={selection.version ?? ""}
                      onChange={(event) =>
                        draft.setSkills((previous) =>
                          previous.map((item) =>
                            item.skill_key === selection.skill_key
                              ? {
                                  ...item,
                                  version: event.target.value
                                    ? Number(event.target.value)
                                    : null,
                                }
                              : item,
                          ),
                        )
                      }
                    >
                      <option value="">{t("Latest")}</option>
                      {versions.map((value) => (
                        <option key={value} value={value}>
                          v{value}
                        </option>
                      ))}
                      {selection.version &&
                        !versions.includes(selection.version) && (
                          <option value={selection.version}>
                            v{selection.version}
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
                        name: skill?.name ?? selection.skill_key,
                      })}
                      onClick={() =>
                        draft.setSkills((previous) =>
                          previous.filter(
                            (item) => item.skill_key !== selection.skill_key,
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
                connection.source.kind === "mcp"
                  ? connection.source.endpoint_url
                  : connection.source.connector_key,
              icon: <ConnectionBrandIcon connection={connection} />,
              disabled: connection.status !== "ready",
              disabledReason: t(connection.status),
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
