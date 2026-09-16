import { BrandIcon } from "a13n-ui";
import { useMCPServers } from "../mcp/catalog";

export function MCPConnectionIcon({
  endpoint,
  size,
}: {
  endpoint: string;
  size?: number;
}) {
  const servers = useMCPServers();
  const preset = servers.data?.find(
    (server) => server.endpoint_url === endpoint,
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
