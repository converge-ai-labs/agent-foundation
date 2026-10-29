import { Checkbox } from "a13n-ui";
import {
  SettingsRow,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import { useRootConfiguration } from "./root-configuration";
import { useSelectors } from "../transport/context";
import { readDocument, updateDocument } from "./documents";
import styles from "./mcp-tools.module.css";

type Group = {
  description: string;
  mcp_servers?: string[];
  harness_plugins?: string[];
  capabilities?: string[];
};
type Search = {
  enabled?: boolean;
  groups?: Record<string, Group>;
  config?: unknown;
};

function hasSources(group: Group) {
  return !!(
    group.mcp_servers?.length ||
    group.harness_plugins?.length ||
    group.capabilities?.length
  );
}

export function selectSearchServer(
  plan: Search,
  id: string,
  name: string,
  selected: boolean,
): Search {
  const groups = structuredClone(plan.groups ?? {});
  if (!selected) {
    for (const [key, group] of Object.entries(groups)) {
      if (group.mcp_servers)
        group.mcp_servers = group.mcp_servers.filter((server) => server !== id);
      if (!hasSources(group)) delete groups[key];
    }
  } else if (
    !Object.values(groups).some((group) => group.mcp_servers?.includes(id))
  ) {
    const base = id.replace(/[^a-zA-Z0-9-]/g, "-").slice(0, 26);
    let key = base;
    let index = 1;
    while (groups[key] && hasSources(groups[key])) key = `${base}-${index++}`;
    groups[key] = {
      description:
        groups[key]?.description ??
        `Search and use ${name} tools.`.slice(0, 512),
      mcp_servers: [id],
    };
  }
  return { ...plan, groups };
}

export function ToolSearchFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const selectors = useSelectors();
  const root = useRootConfiguration();
  const raw = readDocument(source)?.toJS() ?? {};
  const plan: Search = raw.tool_search ?? {};
  const groups = plan.groups ?? {};
  const enabled = !!raw.tool_search && plan.enabled !== false;
  const servers = selectors.data?.mcp_servers ?? [];
  const defaults: string[] =
    raw.mcp_servers ?? root.configuration?.defaults?.mcp_servers ?? [];
  const set = (next: Search) =>
    onChange(updateDocument(source, ["tool_search"], next));
  return (
    <SettingsSection title="Tool search">
      <SettingsToggle
        label="Search tools on demand"
        checked={enabled}
        description="Find selected servers’ enabled tools when needed."
        onCheckedChange={(checked) => {
          let next: Search = { ...plan, enabled: checked };
          if (checked && !raw.tool_search) {
            for (const server of servers.filter((item) =>
              defaults.includes(item.resource_id),
            ))
              next = selectSearchServer(
                next,
                server.resource_id,
                server.name,
                true,
              );
          }
          set(next);
        }}
      />
      {enabled && (
        <>
          {servers.map((server) => {
            const group = Object.entries(groups).find(([, group]) =>
              group.mcp_servers?.includes(server.resource_id),
            );
            return (
              <SettingsRow
                key={server.resource_id}
                label={server.name}
                controlId={`search-${server.resource_id}`}
                description={
                  !defaults.includes(server.resource_id)
                    ? "Used when this server is connected."
                    : undefined
                }
              >
                <Checkbox
                  id={`search-${server.resource_id}`}
                  checked={!!group}
                  onCheckedChange={(checked) =>
                    set(
                      selectSearchServer(
                        plan,
                        server.resource_id,
                        server.name,
                        !!checked,
                      ),
                    )
                  }
                />
              </SettingsRow>
            );
          })}
          {!servers.length && (
            <p className={styles.note}>
              Add an MCP server in Connections to choose its tools here.
            </p>
          )}
        </>
      )}
    </SettingsSection>
  );
}
