import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Textarea } from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function AgentFields({
  source,
  onChange,
  capabilitiesOnly = false,
}: {
  source: string;
  capabilitiesOnly?: boolean;
  onChange: (source: string) => void;
}) {
  const { client } = useTransport();
  const sources = useSources();
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
    queryKey: ["agent-tool-proxy", raw.id],
    enabled: !!accepted && !capabilitiesOnly,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/agents/{agent_id}/tool-proxy", {
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
        Repair the capability, child or tool list in advanced YAML before using
        these fields.
      </p>
    );
  return (
    <>
      <ErrorNotice error={catalog.error || proxy.error} />
      <div className={styles.stack}>
        <strong>Capabilities</strong>
        {capabilities.map((item, index) => (
          <div className={styles.actions} key={index}>
            <span>{item.capability}</span>
            <Button
              variant="ghost"
              aria-label={`Remove capability ${item.capability}`}
              onClick={() => remove("capabilities", index)}
            >
              Remove
            </Button>
          </div>
        ))}
        <ChoiceField
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
                  !capabilities.some(
                    (selected) => selected.capability === item.key,
                  ),
              )
              .map((item) => ({ value: item.key, label: item.key })),
          ]}
        />
        <small>
          Selected capabilities are enabled for this agent when you save. Edit
          plugin-specific options in the configuration file below.
        </small>
      </div>
      {!capabilitiesOnly && (
        <>
          <div className={styles.stack}>
            <strong>Child agents</strong>
            {children.map((item, index) => (
              <div className={styles.actions} key={index}>
                <span>{item.agent ?? item.markdown}</span>
                <small>
                  {item.agent
                    ? "Independent Agent model"
                    : "Inherits parent model"}
                </small>
                <Button
                  variant="ghost"
                  aria-label={`Remove child ${item.agent ?? item.markdown}`}
                  onClick={() => remove("subagents", index)}
                >
                  Remove
                </Button>
              </div>
            ))}
            <ChoiceField
              label="Add child agent"
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
                        label: id,
                      })),
                  ),
              ]}
            />
          </div>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={Array.isArray(raw.tools)}
              onChange={(event) =>
                onChange(
                  updateDocument(
                    source,
                    ["tools"],
                    event.target.checked ? [] : undefined,
                  ),
                )
              }
            />
            Restrict tool names
          </label>
          {Array.isArray(raw.tools) && (
            <FormField
              label="Allowed tool names"
              description="One exact name per line. An empty list allows no tools; disabling this restriction inherits the available tools."
            >
              <Textarea
                rows={3}
                value={raw.tools.join("\n")}
                onChange={(event) =>
                  onChange(
                    updateDocument(
                      source,
                      ["tools"],
                      event.target.value.split("\n").filter(Boolean),
                    ),
                  )
                }
              />
            </FormField>
          )}
          <details className={styles.details}>
            <summary>Saved tool groups</summary>
            <p>
              Uses the saved agent, not your unsaved edits. No servers are
              contacted. Edit tool groups in the configuration file.
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
                    <span>{item.resource_id}</span> · {item.presentation}
                    {item.group ? ` · ${item.group}` : ""}
                  </p>
                ))}
              </>
            )}
          </details>
        </>
      )}
    </>
  );
}
