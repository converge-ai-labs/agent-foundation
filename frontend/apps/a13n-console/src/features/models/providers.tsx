import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import { useResourceEditorState, useResourceRows } from "../../shared/dialogs";
import { EditProviderDialog, ProviderTable } from "../providers";
import { AddProvider } from "./add-provider";
import { modelApi, type ModelScope } from "./api";
import { requiresProviderCredential } from "./provider-credentials";
import { useModelProviderDefinitions } from "./provider-definitions";
import { ProviderForm } from "./provider-form";

export function Providers({ scope }: { scope: ModelScope }) {
  const client = useClient(),
    { organization, organizationAdmin, can } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["ModelProvider"]>();
  const { selected } = rows;
  const api = modelApi(client, scope);
  const query = useQuery({
    queryKey: ["model-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const definitions = useModelProviderDefinitions();
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  const add = manage ? <AddProvider scope={scope} /> : undefined;
  return (
    <>
      {selected && (
        <EditProvider
          key={selected.id}
          scope={
            selected.workspace_id
              ? { kind: "workspace", id: selected.workspace_id }
              : { kind: "organization", id: organization.id }
          }
          provider={selected}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="models"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        canActivateRow={(item) =>
          item.workspace_id ? manage : organizationAdmin
        }
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitions.data?.items.find(
            (definition) => definition.type === item.type,
          )?.display_name,
          workspaceId: item.workspace_id,
          credentials: !requiresProviderCredential(
            item.type,
            item.configuration,
            definitions.data?.items.find(
              (definition) => definition.type === item.type,
            ),
          )
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

/** Editing an existing provider; creation goes through {@link AddProvider}. */
export function EditProvider({
  scope,
  provider,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  provider: Schema["ModelProvider"];
  controlledOpen?: boolean;
  onClose?: () => void;
  finalFocus?: React.RefObject<HTMLElement | null>;
}) {
  const client = useClient(),
    [generation, setGeneration] = useState(0);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const api = modelApi(client, scope);
  const definitions = useModelProviderDefinitions();
  const resource = useQuery({
    queryKey: ["model-provider", scope.kind, scope.id, provider.id],
    enabled: state.open,
    queryFn: ({ signal }) => api.provider(provider.id, signal),
  });
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={resource.data?.value.name ?? provider.name}
      id={provider.id}
      type={provider.type}
      definition={
        definitions.data?.items.find((item) => item.type === provider.type)
          ?.display_name
      }
      scope={scope.kind}
      loading={definitions.isPending || resource.isPending}
      error={
        (!definitions.data && definitions.error) ||
        (!resource.data && resource.error)
          ? (definitions.error ?? resource.error)
          : undefined
      }
    >
      {definitions.data && resource.data && (
        <ProviderForm
          key={generation}
          reload={async () => {
            const result = await resource.refetch();
            if (!result.error) setGeneration((value) => value + 1);
          }}
          scope={scope}
          resource={resource.data}
          definitions={definitions.data.items}
          close={() => state.setOpen(false)}
        />
      )}
    </EditProviderDialog>
  );
}
