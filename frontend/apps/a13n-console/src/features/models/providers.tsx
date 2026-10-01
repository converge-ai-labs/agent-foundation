import { PlusIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { credentialMode } from "../../shared/provider-authentication";
import { useCursor } from "../../shared/collection";
import { useResourceEditorState, useResourceRows } from "../../shared/dialogs";
import { EditProviderDialog, ProviderTable } from "../providers";
import { AddProvider } from "./add-provider";
import { modelApi } from "./api";
import { useModelProviderDefinitions } from "./provider-definitions";
import { ProviderForm } from "./provider-form";

export function Providers() {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    page = useCursor();
  const rows = useResourceRows<Schema["Provider"]>();
  const { selected } = rows;
  const api = modelApi(client, workspace.id);
  const query = useQuery({
    queryKey: ["model-providers", workspace.id, page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const definitions = useModelProviderDefinitions();
  const manage = can("write");
  const add = manage ? (
    <Button type="button" onClick={() => setCreating(true)}>
      <PlusIcon aria-hidden="true" />
      {t("Add provider")}
    </Button>
  ) : undefined;
  return (
    <>
      {/* Keep authorization mounted when creation changes the table's empty state. */}
      {manage && <AddProvider open={creating} onOpenChange={setCreating} />}
      {selected && (
        <EditProvider key={selected.id} provider={selected} {...rows.control} />
      )}
      <ProviderTable
        category="models"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        canActivateRow={() => manage}
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitions.data?.items.find(
            (definition) => definition.type === item.type,
          )?.display_name,
          credentials:
            credentialMode(
              definitions.data?.items.find(
                (definition) => definition.type === item.type,
              ),
              item.config,
            ) !== "required"
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
  provider,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  provider: Schema["Provider"];
  controlledOpen?: boolean;
  onClose?: () => void;
  finalFocus?: React.RefObject<HTMLElement | null>;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    [generation, setGeneration] = useState(0);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const api = modelApi(client, workspace.id);
  const definitions = useModelProviderDefinitions();
  const resource = useQuery({
    queryKey: ["model-provider", workspace.id, provider.id],
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
          resource={resource.data}
          definitions={definitions.data.items}
          close={() => state.setOpen(false)}
        />
      )}
    </EditProviderDialog>
  );
}
