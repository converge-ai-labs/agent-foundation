import type { Dispatch, SetStateAction, ReactNode } from "react";
import { Button, Checkbox, Dialog, Input } from "a13n-ui";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentConfig } from "./configuration";
import type { useAgentChoices } from "./choices";
import styles from "./agents.module.css";

type Skills = NonNullable<AgentConfig["skills"]>;
type MCP = NonNullable<AgentConfig["mcp_tools"]>;
type Connectors = NonNullable<AgentConfig["connector_tools"]>;
export function AgentCapabilities({
  choices,
  skills,
  setSkills,
  mcp,
  setMcp,
  connectors,
  setConnectors,
}: {
  choices: ReturnType<typeof useAgentChoices>;
  skills: Skills;
  setSkills: Dispatch<SetStateAction<Skills>>;
  mcp: MCP;
  setMcp: Dispatch<SetStateAction<MCP>>;
  connectors: Connectors;
  setConnectors: Dispatch<SetStateAction<Connectors>>;
}) {
  const { t } = useTranslation();
  return (
    <>
      <Capability
        title={t("Skills")}
        description={t("Reusable knowledge and procedures.")}
        names={skills.map(
          (selected) =>
            choices.data?.skills.find((item) => item.key === selected.skill_key)
              ?.name ?? selected.skill_key,
        )}
      >
        <div className={styles.selections}>
          {choices.data?.skills.map((skill) => {
            const selected = skills.find(
              (item) => item.skill_key === skill.key,
            );
            return (
              <div key={skill.id}>
                <Checkbox
                  label={skill.name}
                  checked={!!selected}
                  onCheckedChange={(checked) =>
                    setSkills((previous) =>
                      checked
                        ? [...previous, { skill_key: skill.key }]
                        : previous.filter(
                            (item) => item.skill_key !== skill.key,
                          ),
                    )
                  }
                />
                {selected && (
                  <Input
                    label={t("Pinned version")}
                    type="number"
                    min={1}
                    value={selected.version ?? ""}
                    placeholder={t("Latest")}
                    onChange={(event) =>
                      setSkills((previous) =>
                        previous.map((item) =>
                          item.skill_key === skill.key
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
                  />
                )}
              </div>
            );
          })}
          {!choices.isPending && !choices.data?.skills.length && (
            <small>{t("No skills available")}</small>
          )}
        </div>
      </Capability>
      <Capability
        title={t("MCP connections")}
        description={t("Tools provided by your MCP connections.")}
        names={mcp.map(
          (selected) =>
            choices.data?.mcp.find(
              (item) => item.id === selected.mcp_connection_id,
            )?.name ?? selected.mcp_connection_id,
        )}
      >
        <div className={styles.selections}>
          {choices.data?.mcp.map((connection) => {
            const selected = mcp.find(
              (item) => item.mcp_connection_id === connection.id,
            );
            return (
              <div key={connection.id}>
                <Checkbox
                  label={connection.name}
                  checked={!!selected}
                  onCheckedChange={(checked) =>
                    setMcp((previous) =>
                      checked
                        ? [...previous, { mcp_connection_id: connection.id }]
                        : previous.filter(
                            (item) => item.mcp_connection_id !== connection.id,
                          ),
                    )
                  }
                />
                {selected && (
                  <Input
                    label={t("Tool names")}
                    hint={t("Comma-separated; empty selects all.")}
                    value={selected.tools?.join(", ") ?? ""}
                    onChange={(event) =>
                      setMcp((previous) =>
                        previous.map((item) =>
                          item.mcp_connection_id === connection.id
                            ? {
                                ...item,
                                tools: event.target.value.trim()
                                  ? event.target.value
                                      .split(",")
                                      .map((name) => name.trim())
                                      .filter(Boolean)
                                  : null,
                              }
                            : item,
                        ),
                      )
                    }
                  />
                )}
              </div>
            );
          })}
          {!choices.isPending && !choices.data?.mcp.length && (
            <small>{t("No MCP connections available")}</small>
          )}
        </div>
      </Capability>
      <Capability
        title={t("Connectors")}
        description={t("Connected services this agent can use.")}
        names={connectors.map(
          (selected) =>
            choices.data?.connectors.find(
              (item) => item.id === selected.connector_connection_id,
            )?.name ?? selected.connector_connection_id,
        )}
      >
        <div className={styles.selections}>
          {choices.data?.connectors.map((connection) => (
            <Checkbox
              key={connection.id}
              label={connection.name}
              checked={connectors.some(
                (item) => item.connector_connection_id === connection.id,
              )}
              onCheckedChange={(checked) =>
                setConnectors((previous) =>
                  checked
                    ? [...previous, { connector_connection_id: connection.id }]
                    : previous.filter(
                        (item) =>
                          item.connector_connection_id !== connection.id,
                      ),
                )
              }
            />
          ))}
          {!choices.isPending && !choices.data?.connectors.length && (
            <small>{t("No connectors available")}</small>
          )}
        </div>
      </Capability>
    </>
  );
}
function Capability({
  title,
  description,
  names,
  children,
}: {
  title: string;
  description: string;
  names: string[];
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.capability}>
      <header>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <Dialog
          title={title}
          description={t("Select what this agent can use.")}
          closeLabel={t("Done")}
          trigger={
            <Button
              type="button"
              size="sm"
              variant="ghost"
              icon={<Plus size={14} />}
            >
              {t("Manage")}
            </Button>
          }
        >
          {children}
        </Dialog>
      </header>
      <div className={styles.capabilityItems}>
        {names.length ? (
          names.map((name, index) => <span key={index}>{name}</span>)
        ) : (
          <small>{t("None added")}</small>
        )}
      </div>
    </section>
  );
}
