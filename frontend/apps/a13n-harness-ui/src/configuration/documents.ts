import { isMap, parseDocument, stringify } from "yaml";

export type ResourceKind =
  | "model"
  | "agent"
  | "project"
  | "harness_plugin"
  | "environment_profile"
  | "device"
  | "environment_run_extension"
  | "mcp_server"
  | "subagent";
export const resourceKinds: {
  value: ResourceKind;
  label: string;
  directory: string;
  prefix: string;
}[] = [
  { value: "model", label: "Model", directory: "models", prefix: "model" },
  { value: "device", label: "Device", directory: "devices", prefix: "device" },
  { value: "agent", label: "Agent", directory: "agents", prefix: "agent" },
  {
    value: "project",
    label: "Project",
    directory: "projects",
    prefix: "project",
  },
  {
    value: "harness_plugin",
    label: "Harness plugin",
    directory: "extensions",
    prefix: "plugin",
  },
  {
    value: "environment_profile",
    label: "Environment",
    directory: "extensions",
    prefix: "environment",
  },
  {
    value: "environment_run_extension",
    label: "Run extension",
    directory: "extensions",
    prefix: "extension",
  },
  { value: "mcp_server", label: "MCP server", directory: "mcp", prefix: "mcp" },
  {
    value: "subagent",
    label: "Markdown subagent",
    directory: "subagents",
    prefix: "subagent",
  },
];
export function template(kind: ResourceKind, id: string): string {
  if (kind === "subagent")
    return `---\nname: ${id.replace(/^subagent-/, "")}\nid: ${id}\ndescription: Describe when to delegate to this subagent.\n---\n\nWrite the subagent instructions here.\n`;
  const fields: Record<Exclude<ResourceKind, "subagent">, object> = {
    model: {},
    device: {
      device_id: "",
      transport: { kind: "http", configuration: { endpoint: "" } },
      authentication: { kind: "api_key", env: "A13N_DEVICE_TOKEN" },
    },
    agent: { model: null, instructions: "", capabilities: [], subagents: [] },
    project: { roots: [{ path: "" }], defaults: {} },
    harness_plugin: { plugin_key: "", configuration: {} },
    environment_profile: {
      provider_key: "",
      provider_schema_version: "1",
      adapter_key: "",
      provider_configuration: {},
      adapter_configuration: {},
    },
    environment_run_extension: { extension_key: "", configuration: {} },
    mcp_server: { transport: { command: "", arguments: [] } },
  };
  return stringify({
    schema_version: "1",
    kind,
    id,
    name: "Untitled",
    ...fields[kind],
  });
}
export function readDocument(source: string) {
  const document = parseDocument(source);
  if (document.errors.length || !isMap(document.contents)) return null;
  return document;
}
export function updateDocument(
  source: string,
  path: string[],
  value: unknown,
): string {
  const document = readDocument(source);
  if (!document) throw new Error("Fix the YAML syntax before editing fields.");
  if (value === undefined) document.deleteIn(path);
  else document.setIn(path, value);
  return document.toString();
}
