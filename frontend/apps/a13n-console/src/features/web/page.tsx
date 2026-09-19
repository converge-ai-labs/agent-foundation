import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import { useResourceRows } from "../../shared/dialogs";
import { ProviderTable } from "../providers";
import { webProviderApi, type WebProviderScope } from "./api";
import { AddWebProvider, WebProviderEditor } from "./editor";

export function WebProviders({ scope }: { scope: WebProviderScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["WebProvider"]>();
  const query = useQuery({
    queryKey: ["web-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      webProviderApi(client, scope).providers(signal, page.cursor),
  });
  const definitions = useQuery({
    queryKey: ["web-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/web-provider-types", { signal }).then(data),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("web_provider.manage");
  const add = manage ? <AddWebProvider scope={scope} /> : undefined;
  const definitionFor = (type: string) =>
    definitions.data?.items.find((item) => item.type === type);
  return (
    <>
      {rows.selected && (
        <WebProviderEditor
          key={rows.selected.id}
          scope={scope}
          providerId={rows.selected.id}
          readOnly={
            !(
              manage &&
              (scope.kind === "organization" ||
                rows.selected.workspace_id === scope.id)
            )
          }
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
          workspaceId: item.workspace_id,
          credentials:
            definitionFor(item.type)?.credential_required === false
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
