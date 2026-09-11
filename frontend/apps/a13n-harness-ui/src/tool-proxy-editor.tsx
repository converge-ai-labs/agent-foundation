import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  ChoiceField,
  FormField,
  Input,
  Textarea,
} from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { parseDocument } from "yaml";

type Group = {
  description: string;
  mcp_servers: string[];
  harness_plugins: string[];
};
type ProxyConfig = {
  search_name: string;
  call_name: string;
  max_results: number;
  max_search_bytes: number;
};
type Source = {
  resource_id: string;
  name: string;
  kind: "mcp_server" | "harness_plugin";
  enabled: boolean;
};
type AgentView = {
  content: string;
  writable: boolean;
  agent_tool_proxy: {
    agent_id: string;
    tool_proxy: { groups: Record<string, Group>; config: ProxyConfig };
    sources: Source[];
  };
};
type SourceInfo = {
  relative_path: string;
  resource_kind: string;
  resource_ids: string[];
  writable: boolean;
};
type DraftGroup = Group & { key: string; name: string };

async function request<T>(
  key: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: {
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => null);
    throw new Error(
      failure?.error?.message ??
        failure?.message ??
        `Request failed (${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}

export function ToolProxyEditor({ apiKey }: { apiKey: string }) {
  const [agents, setAgents] = useState<SourceInfo[]>([]);
  const [path, setPath] = useState("");
  const [view, setView] = useState<AgentView | null>(null);
  const [groups, setGroups] = useState<DraftGroup[]>([]);
  const [config, setConfig] = useState<ProxyConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);

  function accept(next: AgentView) {
    if (!next.agent_tool_proxy || typeof next.content !== "string")
      throw new Error("This source is not an editable Agent.");
    setView(next);
    setGroups(
      Object.entries(next.agent_tool_proxy.tool_proxy.groups).map(
        ([name, group]) => ({ ...group, name, key: name }),
      ),
    );
    setConfig(next.agent_tool_proxy.tool_proxy.config);
    setDirty(false);
  }

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void request<{ sources: SourceInfo[] }>(
      apiKey,
      "/api/configuration/sources",
      { signal: controller.signal },
    )
      .then((catalog) => {
        if (controller.signal.aborted) return;
        if (!Array.isArray(catalog.sources))
          throw new Error(
            "The server returned an incompatible configuration catalog.",
          );
        const selected = catalog.sources.filter(
          (item) => item.resource_kind === "agent",
        );
        setAgents(selected);
        setPath((current) =>
          selected.some((item) => item.relative_path === current)
            ? current
            : (selected[0]?.relative_path ?? ""),
        );
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Could not load Agents.",
          );
      });
    return () => controller.abort();
  }, [apiKey, reload]);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setView(null);
    setError("");
    setNotice("");
    void request<AgentView>(apiKey, `/api/configuration/sources/${path}`, {
      signal: controller.signal,
    })
      .then((next) => {
        if (!controller.signal.aborted) accept(next);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Could not load Agent.",
          );
      });
    return () => controller.abort();
  }, [apiKey, path, reload]);

  function change(key: string, patch: Partial<DraftGroup>) {
    setGroups((current) =>
      current.map((group) =>
        group.key === key ? { ...group, ...patch } : group,
      ),
    );
    setDirty(true);
    setNotice("");
  }

  async function save() {
    if (!view || !config) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const names = groups.map((group) => group.name);
      if (new Set(names).size !== names.length)
        throw new Error("Group names must be unique.");
      for (const group of groups) {
        if (
          !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(group.name) ||
          group.name.includes("__")
        )
          throw new Error(
            "Use a group name starting with a letter, up to 32 letters, digits, underscores or hyphens, without '__'.",
          );
        if (!group.description.trim())
          throw new Error(`Add a description for ${group.name}.`);
      }
      // Edit the accepted YAML document, retaining unrelated fields and comments.
      const document = parseDocument(view.content);
      if (document.errors.length) throw new Error(document.errors[0].message);
      if (document.get("tool_proxy") == null)
        document.set("tool_proxy", document.createNode({}));
      document.setIn(
        ["tool_proxy", "groups"],
        Object.fromEntries(
          groups.map(({ name, description, mcp_servers, harness_plugins }) => [
            name,
            { description, mcp_servers, harness_plugins },
          ]),
        ),
      );
      document.setIn(["tool_proxy", "config"], config);
      const body = JSON.stringify({ content: document.toString() });
      await request(
        apiKey,
        `/api/configuration/validate?path=${encodeURIComponent(path)}`,
        { method: "POST", body },
      );
      await request(apiKey, `/api/configuration/sources/${path}`, {
        method: "PUT",
        body,
      });
      const next = await request<AgentView>(
        apiKey,
        `/api/configuration/sources/${path}`,
      );
      accept(next);
      setNotice(
        "Saved and read back from Agent configuration. Changes apply to subsequent Runs.",
      );
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Save failed. Your draft is retained.",
      );
    } finally {
      setBusy(false);
    }
  }

  const sources = view?.agent_tool_proxy.sources ?? [];
  const owner = (id: string) =>
    groups.find((group) =>
      [...group.mcp_servers, ...group.harness_plugins].includes(id),
    );
  const direct = sources.filter(
    (source) => source.enabled && !owner(source.resource_id),
  );
  const active = sources.filter(
    (source) => source.enabled && owner(source.resource_id),
  );
  const dormant = sources.filter(
    (source) => !source.enabled && owner(source.resource_id),
  );

  return (
    <section aria-labelledby="proxy-heading" className="proxy-editor">
      <header className="section-heading">
        <div>
          <h2 id="proxy-heading">Tool proxy groups</h2>
          <p>Organize MCP and Harness Plugin tools into discoverable groups.</p>
        </div>
        <Badge variant="outline">Agent configuration</Badge>
      </header>
      <p className="muted">
        Grouping changes presentation, not which sources are enabled. Existing
        Threads keep their source selections; each new Run captures its own
        plan.
      </p>
      {error && (
        <div role="alert" className="editor-error">
          {error}
        </div>
      )}
      {!agents.length && !error && (
        <p>
          No Agent resources found. Create an Agent in your configuration
          directory to configure groups.
        </p>
      )}
      {agents.length > 0 && (
        <ChoiceField
          label="Agent"
          value={path}
          disabled={busy || dirty}
          options={agents.map((agent) => ({
            value: agent.relative_path,
            label: agent.resource_ids[0] ?? agent.relative_path,
          }))}
          onValueChange={setPath}
          description={
            dirty ? "Save or discard your draft before switching Agents." : path
          }
        />
      )}
      {!view && path && !error && (
        <p role="status">Loading Agent configuration…</p>
      )}
      {view && config && (
        <>
          <div
            className="proxy-summary"
            aria-label="Source presentation summary"
          >
            <span>
              <strong>{active.length}</strong> grouped and enabled
            </span>
            <span>
              <strong>{dormant.length}</strong> dormant
            </span>
            <span>
              <strong>{direct.length}</strong> direct
            </span>
          </div>
          <p className="muted">
            Static preview uses Agent defaults, not a particular Thread or
            discovered tool count. Empty groups produce no proxy controls.
          </p>
          <fieldset disabled={busy || !view.writable} className="proxy-fields">
            {groups.length === 0 && (
              <div className="empty-groups">
                <h3>Keep direct tools, group the rest</h3>
                <p>
                  Add a group, describe its purpose, and select sources.
                  Unlisted enabled sources stay direct.
                </p>
              </div>
            )}
            {groups.map((group) => (
              <section
                key={group.key}
                className="proxy-group"
                aria-label={`Group ${group.name || "new"}`}
              >
                <div className="group-heading">
                  <h3>{group.name || "New group"}</h3>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setGroups(
                        groups.filter((item) => item.key !== group.key),
                      );
                      setDirty(true);
                      setNotice("");
                    }}
                  >
                    Remove group
                  </Button>
                </div>
                <div className="group-identity">
                  <FormField label="Group name">
                    <Input
                      value={group.name}
                      maxLength={32}
                      onChange={(event) =>
                        change(group.key, { name: event.target.value })
                      }
                    />
                  </FormField>
                  <FormField
                    label="Description"
                    description="Help the model choose this group without loading all its tools."
                  >
                    <Textarea
                      value={group.description}
                      maxLength={512}
                      rows={2}
                      onChange={(event) =>
                        change(group.key, { description: event.target.value })
                      }
                    />
                  </FormField>
                </div>
                <fieldset className="source-list">
                  <legend>Sources</legend>
                  {sources.length === 0 && (
                    <p className="muted">
                      Configure MCP servers or Harness Plugins before selecting
                      sources.
                    </p>
                  )}
                  {sources.map((source) => {
                    const field =
                      source.kind === "mcp_server"
                        ? "mcp_servers"
                        : "harness_plugins";
                    const selected = group[field].includes(source.resource_id);
                    const other = owner(source.resource_id);
                    return (
                      <label key={source.resource_id} className="source-row">
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!!other && other.key !== group.key}
                          onChange={(event) =>
                            change(group.key, {
                              [field]: event.target.checked
                                ? [...group[field], source.resource_id]
                                : group[field].filter(
                                    (id) => id !== source.resource_id,
                                  ),
                            })
                          }
                        />
                        <span>
                          <span>{source.name}</span>
                          <small>
                            {source.resource_id} ·{" "}
                            {source.kind === "mcp_server"
                              ? "MCP"
                              : "Harness Plugin"}
                          </small>
                        </span>
                        <span className="muted">
                          {other && other.key !== group.key
                            ? `In ${other.name}`
                            : source.enabled
                              ? "Enabled"
                              : selected
                                ? "Dormant"
                                : "Disabled"}
                        </span>
                      </label>
                    );
                  })}
                </fieldset>
              </section>
            ))}
            <Button
              variant="outline"
              onClick={() => {
                const key = crypto.randomUUID();
                setGroups([
                  ...groups,
                  {
                    key,
                    name: "",
                    description: "",
                    mcp_servers: [],
                    harness_plugins: [],
                  },
                ]);
                setDirty(true);
                setNotice("");
              }}
            >
              <PlusIcon />
              Add group
            </Button>
            <details className="proxy-options">
              <summary>Discovery controls</summary>
              <div className="options-grid">
                {(["search_name", "call_name"] as const).map((field) => (
                  <FormField
                    key={field}
                    label={
                      field === "search_name"
                        ? "Search tool name"
                        : "Call tool name"
                    }
                  >
                    <Input
                      value={config[field]}
                      onChange={(event) => {
                        setConfig({ ...config, [field]: event.target.value });
                        setDirty(true);
                        setNotice("");
                      }}
                    />
                  </FormField>
                ))}
                {(["max_results", "max_search_bytes"] as const).map((field) => (
                  <FormField
                    key={field}
                    label={
                      field === "max_results"
                        ? "Results per search"
                        : "Search response byte limit"
                    }
                  >
                    <Input
                      type="number"
                      min={field === "max_results" ? 1 : 1024}
                      max={field === "max_results" ? 100 : 32768}
                      value={config[field]}
                      onChange={(event) => {
                        setConfig({
                          ...config,
                          [field]: Number(event.target.value),
                        });
                        setDirty(true);
                        setNotice("");
                      }}
                    />
                  </FormField>
                ))}
              </div>
            </details>
          </fieldset>
          {direct.length > 0 && (
            <p className="muted">
              Direct sources: {direct.map((source) => source.name).join(", ")}
            </p>
          )}
          <p className="muted">
            Renaming a group changes canonical target names (
            <code>group__tool</code>). Update exact tool allowlists accordingly.
            Selecting a proxy control never grants access to all members.
          </p>
          {!view.writable && (
            <p>
              This Agent source is read-only. Edit its owning configuration
              source.
            </p>
          )}
          <footer className="editor-actions">
            <Button
              disabled={!dirty || busy || !view.writable}
              onClick={() => void save()}
            >
              {busy ? "Saving…" : "Save groups"}
            </Button>
            <Button
              variant="outline"
              disabled={!dirty || busy}
              onClick={() => {
                accept(view);
                setError("");
                setNotice("Draft discarded.");
              }}
            >
              Discard changes
            </Button>
            {dirty && <span className="muted">Unsaved changes</span>}
          </footer>
        </>
      )}
      {error && !view && (
        <Button
          variant="outline"
          onClick={() => setReload((value) => value + 1)}
        >
          Retry
        </Button>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
