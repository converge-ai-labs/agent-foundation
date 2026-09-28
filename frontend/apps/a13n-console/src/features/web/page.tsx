import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import { useResourceRows } from "../../shared/dialogs";
import { credentialMode } from "../../shared/provider-authentication";
import { ProviderTable } from "../providers";
import { webProviderApi } from "./api";
import { AddWebProvider, WebProviderEditor } from "./editor";

export function WebProviders() {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    page = useCursor();
  const rows = useResourceRows<Schema["Provider"]>();
  const query = useQuery({
    queryKey: ["web-providers", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      webProviderApi(client, workspace.id).providers(signal, page.cursor),
  });
  const definitions = useQuery({
    queryKey: ["web-provider-types"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: "web" } },
          signal,
        })
        .then(data),
  });
  const manage = can("write");
  const add = manage ? <AddWebProvider /> : undefined;
  const definitionFor = (type: string) =>
    definitions.data?.items.find((item) => item.type === type);
  return (
    <>
      {rows.selected && (
        <WebProviderEditor
          key={rows.selected.id}
          providerId={rows.selected.id}
          readOnly={!manage}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="web"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitionFor(item.type)?.display_name,
          credentials:
            credentialMode(definitionFor(item.type), item.config) !== "required"
              ? "not_required"
              : item.credential_configured
                ? "configured"
                : "not_configured",
          state: item.enabled ? "enabled" : "disabled",
        })}
      />
    </>
  );
}
