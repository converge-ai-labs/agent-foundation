import {
  useState,
  type Dispatch,
  type SetStateAction,
  type ReactNode,
} from "react";
import { Checkbox, Input, SearchInput } from "a13n-ui";
import { Sparkles, Network, Plug, ArrowUpRight } from "lucide-react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
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
  const { t } = useTranslation(),
    { workspace } = useWorkspace();
  const base = `/workspaces/${workspace.id}`;
  return (
    <section className={styles.capabilitySection}>
      <header>
        <h2>
          {t("Capabilities")}{" "}
          <span>{skills.length + mcp.length + connectors.length}</span>
        </h2>
      </header>
      <CapabilityGroup
        title={t("Skills")}
        description={t("Reusable knowledge and procedures.")}
        icon={<Sparkles size={15} />}
        setup={`${base}/skills`}
        loading={choices.isPending}
        choices={availableChoices(
          choices.data?.skills ?? [],
          skills.map((item) => item.skill_key),
        ).map((skill) => {
          const selected = skills.find((item) => item.skill_key === skill.key);
          return {
            ...skill,
            checked: !!selected,
            onChange: (checked) =>
              setSkills((previous) =>
                checked
                  ? [...previous, { skill_key: skill.key }]
                  : previous.filter((item) => item.skill_key !== skill.key),
              ),
            settings: selected && (
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
            ),
          };
        })}
      />
      <CapabilityGroup
        title={t("MCP connections")}
        description={t("Tools provided by your MCP connections.")}
        icon={<Network size={15} />}
        setup={`${base}/mcp`}
        loading={choices.isPending}
        choices={availableChoices(
          (choices.data?.mcp ?? []).map((item) => ({
            key: item.id,
            name: item.name,
          })),
          mcp.map((item) => item.mcp_connection_id),
        ).map((connection) => {
          const selected = mcp.find(
            (item) => item.mcp_connection_id === connection.key,
          );
          return {
            ...connection,
            checked: !!selected,
            onChange: (checked) =>
              setMcp((previous) =>
                checked
                  ? [...previous, { mcp_connection_id: connection.key }]
                  : previous.filter(
                      (item) => item.mcp_connection_id !== connection.key,
                    ),
              ),
            settings: selected && (
              <ToolNames
                tools={selected.tools}
                onChange={(tools) =>
                  setMcp((previous) =>
                    previous.map((item) =>
                      item.mcp_connection_id === connection.key
                        ? { ...item, tools }
                        : item,
                    ),
                  )
                }
              />
            ),
          };
        })}
      />
      <CapabilityGroup
        title={t("Connectors")}
        description={t("Connected services this agent can use.")}
        icon={<Plug size={15} />}
        setup={`${base}/connectors`}
        loading={choices.isPending}
        choices={availableChoices(
          (choices.data?.connectors ?? []).map((item) => ({
            key: item.id,
            name: item.name,
          })),
          connectors.map((item) => item.connector_connection_id),
        ).map((connection) => ({
          ...connection,
          checked: connectors.some(
            (item) => item.connector_connection_id === connection.key,
          ),
          onChange: (checked) =>
            setConnectors((previous) =>
              checked
                ? [...previous, { connector_connection_id: connection.key }]
                : previous.filter(
                    (item) => item.connector_connection_id !== connection.key,
                  ),
            ),
        }))}
      />
    </section>
  );
}

// Keep configured references editable even when a resource is no longer listed.
function availableChoices(
  available: { key: string; name: string }[],
  selected: string[],
) {
  return [
    ...available.map((item) => ({
      key: item.key,
      name: item.name,
      unavailable: false,
    })),
    ...selected
      .filter((key) => !available.some((item) => item.key === key))
      .map((key) => ({ key, name: key, unavailable: true })),
  ];
}

type CapabilityChoice = {
  key: string;
  name: string;
  unavailable: boolean;
  checked: boolean;
  onChange: (checked: boolean) => void;
  settings?: ReactNode;
};

function CapabilityGroup({
  title,
  description,
  icon,
  choices,
  setup,
  loading,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  choices: CapabilityChoice[];
  setup: string;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const visible = choices.filter((item) =>
    item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  return (
    <section className={styles.capabilityGroup}>
      <header>
        <h3>
          <span aria-hidden="true">{icon}</span>
          {title}
        </h3>
        <p>{description}</p>
      </header>
      <div className={styles.capabilityChoices}>
        {choices.length > 6 && (
          <SearchInput
            label={t("Search {{kind}}", { kind: title })}
            placeholder={t("Search…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        )}
        <div className={styles.choiceList}>
          {visible.map((choice) => (
            <div key={choice.key} className={styles.choice}>
              <div className={styles.choiceHeading}>
                <Checkbox
                  label={choice.name}
                  checked={choice.checked}
                  onCheckedChange={(value) => choice.onChange(value === true)}
                />
                {choice.unavailable && !loading && (
                  <small>{t("Unavailable")}</small>
                )}
              </div>
              {choice.checked && choice.settings && (
                <div className={styles.choiceSettings}>{choice.settings}</div>
              )}
            </div>
          ))}
          {!choices.length && (
            <p className={styles.capabilityEmpty}>
              {t(loading ? "Loading…" : "None available in this workspace.")}
            </p>
          )}
          {!!choices.length && !visible.length && (
            <p className={styles.capabilityEmpty}>
              {t("No matching capabilities")}
            </p>
          )}
        </div>
        <Link
          className={styles.setupCapability}
          to={setup}
          target="_blank"
          rel="noreferrer"
        >
          {t("Set up {{kind}}", { kind: title })}
          <ArrowUpRight size={12} aria-hidden="true" />
          <span className="visually-hidden">{t("Opens in a new tab")}</span>
        </Link>
      </div>
    </section>
  );
}

function ToolNames({
  tools,
  onChange,
}: {
  tools: string[] | null | undefined;
  onChange: (tools: string[] | null) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(tools?.join(", ") ?? "");
  return (
    <Input
      label={t("Tool names")}
      hint={t("Comma-separated; empty selects all.")}
      value={text}
      placeholder={t("All tools")}
      onChange={(event) => {
        const value = event.target.value;
        setText(value);
        const names = value
          .split(",")
          .map((name) => name.trim())
          .filter(Boolean);
        onChange(names.length ? names : null);
      }}
    />
  );
}
