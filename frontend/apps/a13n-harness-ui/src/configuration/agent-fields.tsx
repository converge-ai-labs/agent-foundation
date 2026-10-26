import type { ReactNode } from "react";
import {
  Wrench,
  TreeStructure,
  ListChecks,
  PuzzlePiece,
  Books,
  Globe,
  ShieldCheck,
  Stack,
  ArrowCounterClockwise,
  Trash,
} from "@phosphor-icons/react";
import management from "./management.module.css";
import { Link } from "react-router";
import { useRootConfiguration } from "./root-configuration";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ChoiceField,
  FormField,
  SettingsSection,
  Textarea,
} from "a13n-ui";
import { ResourceChoice } from "./resource-choice";
import { AgentNativeTools, nativeToolKind } from "./native-tools";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function AgentFields({
  source,
  onChange,
  connections,
}: {
  connections?: ReactNode;
  source: string;
  onChange: (source: string) => void;
}) {
  const { client } = useTransport();
  const sources = useSources();
  const selectors = useSelectors();
  const root = useRootConfiguration();
  const agentName = (id: string) =>
    selectors.data?.agents.find((item) => item.agent_id === id)?.name ?? id;
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  const document = readDocument(source);
  const raw = document?.toJS() ?? {};
  const capabilities: { capability: string }[] = Array.isArray(raw.capabilities)
    ? raw.capabilities
    : [];
  const children: { agent?: string; markdown?: string }[] = Array.isArray(
    raw.subagents,
  )
    ? raw.subagents
    : [];
  const remove = (axis: string, index: number) => {
    if (!document) return;
    document.deleteIn([axis, index]);
    onChange(document.toString());
  };
  const add = (axis: string, value: object) => {
    if (!document) return;
    if (!document.has(axis)) document.set(axis, []);
    document.addIn([axis], value);
    onChange(document.toString());
  };
  const accepted = sources.data?.sources.some(
    (item) =>
      item.resource_kind === "agent" && item.resource_ids.includes(raw.id),
  );
  const proxy = useQuery({
    queryKey: ["agent-tool-search", raw.id],
    enabled: !!accepted,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/agents/{agent_id}/tool-search", {
          params: { path: { agent_id: raw.id } },
          signal,
        }),
      ),
  });
  if (!document)
    return (
      <p>
        Fix the YAML syntax in the configuration file before editing
        capabilities.
      </p>
    );
  if (
    (raw.capabilities !== undefined &&
      (!Array.isArray(raw.capabilities) ||
        !capabilities.every(
          (item) => item && typeof item.capability === "string",
        ))) ||
    (raw.subagents !== undefined &&
      (!Array.isArray(raw.subagents) ||
        !children.every(
          (item) =>
            item &&
            (typeof item.agent === "string" ||
              typeof item.markdown === "string"),
        ))) ||
    (raw.tools != null &&
      (!Array.isArray(raw.tools) ||
        !raw.tools.every((item: unknown) => typeof item === "string")))
  )
    return (
      <p>
        Repair the capability, subagent or tool list in advanced YAML before
        using these fields.
      </p>
    );
  const workingIndex = capabilities.findIndex(
    (item) => item.capability === "working_state",
  );
  const working = raw.capabilities?.[workingIndex]?.configuration ?? {};
  const setWorking = (key: string, value: boolean) => {
    if (workingIndex < 0)
      add("capabilities", {
        capability: "working_state",
        configuration: { [key]: value },
      });
    else {
      document.setIn(
        ["capabilities", workingIndex, "configuration", key],
        value,
      );
      onChange(document.toString());
    }
  };
  return (
    <>
      <ErrorNotice error={catalog.error || proxy.error} />
      <SettingsSection
        title={
          <span className={management.sectionTitle}>
            <Wrench aria-hidden="true" weight="duotone" />
            Tools & capabilities
          </span>
        }
      >
        <AgentNativeTools source={source} onChange={onChange} />
        <div className={`${styles.stack} ${styles.fieldGroup}`}>
          <h4 className={management.subsectionTitle}>
            <ListChecks aria-hidden="true" />
            Tasks & notes
          </h4>
          <div className={management.checkboxRow}>
            <label className={management.check}>
              <Checkbox
                checked={working.tasks_enabled ?? true}
                onCheckedChange={(checked) =>
                  setWorking("tasks_enabled", checked)
                }
              />
              Tasks
            </label>
            <label className={management.check}>
              <Checkbox
                checked={working.notes_enabled ?? true}
                onCheckedChange={(checked) =>
                  setWorking("notes_enabled", checked)
                }
              />
              Notes
            </label>
          </div>
          <h4 className={management.subsectionTitle}>
            <PuzzlePiece aria-hidden="true" />
            Capabilities
          </h4>
          {capabilities.map(
            (item, index) =>
              !nativeToolKind(item) &&
              item.capability !== "working_state" && (
                <div className={management.capability} key={index}>
                  <div className={management.capabilityHeader}>
                    <span className={management.inlineLabel}>
                      {item.capability === "skills" ? (
                        <Books aria-hidden="true" />
                      ) : item.capability === "web" ? (
                        <Globe aria-hidden="true" />
                      ) : (
                        <PuzzlePiece aria-hidden="true" />
                      )}
                      {item.capability.replaceAll("_", " ")}
                    </span>
                    <Button
                      variant="ghost"
                      aria-label={`${["file_context", "user_interaction", "codeact"].includes(item.capability) ? "Reset capability" : "Remove capability"} ${item.capability}`}
                      onClick={() => remove("capabilities", index)}
                    >
                      {["file_context", "user_interaction", "codeact"].includes(
                        item.capability,
                      ) ? (
                        <ArrowCounterClockwise aria-hidden="true" />
                      ) : (
                        <Trash aria-hidden="true" />
                      )}
                      {["file_context", "user_interaction", "codeact"].includes(
                        item.capability,
                      )
                        ? "Reset to default"
                        : "Remove"}
                    </Button>
                  </div>
                  <CapabilityOptions
                    source={source}
                    index={index}
                    onChange={onChange}
                  />
                </div>
              ),
          )}
          <ResourceChoice
            loading={catalog.isPending}
            label="Add capability"
            value=""
            onValueChange={(capability) => {
              if (capability)
                add("capabilities", { capability, configuration: {} });
            }}
            options={[
              { value: "", label: "Choose an installed capability" },
              ...(catalog.data ?? [])
                .filter(
                  (item) =>
                    item.kind === "capability" &&
                    item.configurable &&
                    ![
                      "NativeTool",
                      "native_image_generation",
                      "working_state",
                    ].includes(item.key) &&
                    !capabilities.some(
                      (selected) => selected.capability === item.key,
                    ),
                )
                .map((item) => ({ value: item.key, label: item.key })),
            ]}
          />
          <small>
            Additional capability options are available in Advanced
            configuration.
          </small>
        </div>
        {connections}
      </SettingsSection>
      <SettingsSection
        title={
          <span className={management.sectionTitle}>
            <TreeStructure aria-hidden="true" weight="duotone" />
            Delegation
          </span>
        }
      >
        <div className={`${styles.stack} ${styles.fieldGroup}`}>
          {children.map((item, index) => (
            <div className={management.capabilityHeader} key={index}>
              <div className={management.subagentInfo}>
                <span>
                  {item.agent ? agentName(item.agent) : item.markdown}
                </span>
                <small>
                  {item.agent
                    ? `Agent · ${selectors.data?.models?.find((model) => model.model_id === selectors.data?.agents.find((agent) => agent.agent_id === item.agent)?.model_id)?.name ?? "No model selected"}`
                    : "Markdown · inherits this Agent's model"}
                </small>
              </div>
              <Button
                variant="ghost"
                aria-label={`Remove subagent ${item.agent ?? item.markdown}`}
                onClick={() => remove("subagents", index)}
              >
                <Trash aria-hidden="true" />
                Remove
              </Button>
            </div>
          ))}
          <ResourceChoice
            loading={sources.isPending}
            label="Add subagent"
            value=""
            onValueChange={(child) => {
              if (child) {
                const [kind, id] = child.split(":");
                add("subagents", { [kind]: id });
              }
            }}
            options={[
              {
                value: "",
                label: "Choose a configured Agent or Markdown subagent",
              },
              ...(sources.data?.sources ?? [])
                .filter(
                  (item) =>
                    item.resource_kind === "agent" ||
                    item.resource_kind === "subagent",
                )
                .flatMap((item) =>
                  item.resource_ids
                    .filter(
                      (id) =>
                        id !== raw.id &&
                        !children.some(
                          (child) => (child.agent ?? child.markdown) === id,
                        ),
                    )
                    .map((id) => ({
                      value: `${item.resource_kind === "agent" ? "agent" : "markdown"}:${id}`,
                      label:
                        item.resource_kind === "agent"
                          ? `Agent · ${agentName(id)}`
                          : `Markdown · ${id}`,
                    })),
                ),
            ]}
          />
          {!!root.configuration?.subagents?.include?.length && (
            <div>
              <h3>Instance roles</h3>
              <p>{root.configuration.subagents.include.join(", ")}</p>
              <small>
                Appended when this Agent runs as a root.{" "}
                <Link
                  to={`/settings/source?path=${encodeURIComponent(root.path!)}`}
                >
                  Manage instance roles
                </Link>
              </small>
            </div>
          )}
          <ErrorNotice error={root.error} />
        </div>
      </SettingsSection>
      <details className={styles.details}>
        <summary>
          <span className={management.inlineLabel}>
            <ShieldCheck aria-hidden="true" />
            Tool restrictions
          </span>
        </summary>
        <div className={styles.stack}>
          <label className={management.check}>
            <Checkbox
              checked={Array.isArray(raw.tools)}
              onCheckedChange={(checked) =>
                onChange(
                  updateDocument(source, ["tools"], checked ? [] : undefined),
                )
              }
            />
            Restrict tool names
          </label>
          {Array.isArray(raw.tools) && (
            <FormField
              label="Allowed tool names"
              description="One exact name per line. An empty list removes optional tools; required infrastructure remains available."
            >
              <Textarea
                rows={3}
                onBlur={(event) =>
                  onChange(
                    updateDocument(
                      source,
                      ["tools"],
                      event.target.value.split("\n").filter(Boolean),
                    ),
                  )
                }
                value={raw.tools.join("\n")}
                onChange={(event) =>
                  onChange(
                    updateDocument(
                      source,
                      ["tools"],
                      event.target.value ? event.target.value.split("\n") : [],
                    ),
                  )
                }
              />
            </FormField>
          )}
        </div>
      </details>
      <details className={styles.details}>
        <summary>
          <span className={management.inlineLabel}>
            <Stack aria-hidden="true" />
            Saved tool groups
          </span>
        </summary>
        <p>
          Saved configuration, not unsaved edits. Edit groups in Advanced
          configuration.
        </p>
        {!accepted ? (
          <p>Save this agent to inspect its tool groups.</p>
        ) : proxy.isPending ? (
          <p>Loading composition…</p>
        ) : (
          <>
            {!proxy.data?.sources.length && (
              <p>No configured plugin or MCP sources.</p>
            )}
            {proxy.data?.sources.map((item) => (
              <p key={item.resource_id}>
                <span>{item.name}</span> · {item.presentation}
                {item.group ? ` · ${item.group}` : ""}
              </p>
            ))}
          </>
        )}
      </details>
    </>
  );
}

function CapabilityOptions({
  source,
  index,
  onChange,
}: {
  source: string;
  index: number;
  onChange: (source: string) => void;
}) {
  const document = readDocument(source)!;
  const item = document.toJS().capabilities[index];
  const config = item.configuration ?? {};
  const set = (path: string[], value: unknown) => {
    document.setIn(["capabilities", index, "configuration", ...path], value);
    onChange(document.toString());
  };
  if (item.capability === "skills")
    return (
      <FormField
        label="Additional skill directories"
        description="One directory per line, in addition to discovered skills."
      >
        <Textarea
          rows={2}
          onBlur={(event) =>
            set(["roots"], event.target.value.split("\n").filter(Boolean))
          }
          value={(config.roots ?? []).join("\n")}
          onChange={(event) =>
            set(
              ["roots"],
              event.target.value ? event.target.value.split("\n") : [],
            )
          }
        />
      </FormField>
    );
  if (item.capability === "web")
    return (
      <details className={styles.details}>
        <summary>
          <span className={management.inlineLabel}>
            <Globe aria-hidden="true" />
            Web options
          </span>
        </summary>
        <div className={styles.stack}>
          <ChoiceField
            label="Web search"
            value={config.search?.mode ?? "auto"}
            options={[
              { value: "auto", label: "Automatic" },
              { value: "host", label: "Host search" },
              { value: "native", label: "Model-native search" },
              { value: "off", label: "Off" },
            ]}
            onValueChange={(mode) => set(["search", "mode"], mode)}
          />
          <label className={management.check}>
            <Checkbox
              checked={config.fetch?.enabled ?? true}
              onCheckedChange={(checked) => set(["fetch", "enabled"], checked)}
            />
            Fetch web resources
          </label>
          <label className={management.check}>
            <Checkbox
              checked={config.download?.enabled ?? true}
              onCheckedChange={(checked) =>
                set(["download", "enabled"], checked)
              }
            />
            Download files
          </label>
        </div>
      </details>
    );
  return null;
}
