import { PlugIcon } from "@phosphor-icons/react";
import { BrandIcon, resolveBrand } from "a13n-ui";
import { useMCPServers } from "../mcp/catalog";

/** A saved remote MCP server's mark: its catalog logo or brand, else a plug. */
export function MCPConnectionIcon({
  endpoint,
  size = 20,
}: {
  endpoint: string;
  size?: number;
}) {
  const servers = useMCPServers();
  const preset = servers.data?.find((server) => server.url === endpoint);
  if (!preset?.logo_url && !resolveBrand({ identity: preset?.key, endpoint }))
    return (
      <PlugIcon
        aria-hidden="true"
        size={size}
        className="shrink-0 text-muted-foreground"
      />
    );
  return (
    <BrandIcon
      identity={preset?.key}
      endpoint={endpoint}
      logo={preset?.logo_url}
      fallbackIdentity="mcp"
      size={size}
    />
  );
}
