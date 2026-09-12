import { useState } from "react";
import {
  BrandIcon,
  DisclosureSection,
  Button,
  FormField,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";
import { ArrowLeftIcon, PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice } from "../../shared/feedback";
import type { Schema } from "../../shared/api";
import { ManageProvidersLink } from "../providers/manage-link";
import { ConnectionSetup } from "../connectors/setup";
import { ConnectorToolPreview } from "../connectors/tools";
import { CreateMCP } from "../mcp/create";
import { useConnectionDirectory } from "./directory";
import { mcpPresets, type MCPPreset } from "./presets";

type Selection =
  | {
      kind: "connector";
      connector: Schema["Connector"];
      provider: Schema["ConnectorProvider"];
    }
  | { kind: "mcp"; preset?: MCPPreset };
export function NewConnection({
  onConnected,
}: {
  onConnected: (id: string) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("New connection")}
      description={t(
        "Connect an application or a remote MCP server to this workspace.",
      )}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button type="button">
          <PlusIcon />
          {t("New connection")}
        </Button>
      }
    >
      {open && (
        <ConnectionChoice
          onCancel={() => setOpen(false)}
          onConnected={(id) => {
            setOpen(false);
            onConnected(id);
          }}
        />
      )}
    </ModalFrame>
  );
}
function ConnectionChoice({
  onConnected,
  onCancel,
}: {
  onConnected: (id: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace(),
    [search, setSearch] = useState(""),
    [selected, setSelected] = useState<Selection>(),
    [started, setStarted] = useState(false);
  const directory = useConnectionDirectory(search);
  const term = search.trim().toLocaleLowerCase();
  const rank = (label: string) =>
    label.toLocaleLowerCase() === term
      ? 0
      : label.toLocaleLowerCase().startsWith(term)
        ? 1
        : 2;
  const options = [
    ...directory.entries.map(({ connector, provider }) => ({
      value: `connector:${provider.id}:${connector.key}`,
      label: connector.name,
      description: connector.description ?? "",
      badge: provider.name,
      keywords: [connector.key],
      icon: <BrandIcon alias={connector.key} logo={connector.logo_url} />,
    })),
    ...(can("connection.manage")
      ? mcpPresets.map((preset) => ({
          value: `mcp:${preset.id}`,
          label: preset.name,
          description: t(preset.description),
          badge: t("Remote MCP"),
          keywords: [preset.id, preset.endpoint],
          icon: (
            <BrandIcon
              identity={preset.id}
              endpoint={preset.endpoint}
              logo={preset.logo}
            />
          ),
        }))
      : []),
  ].sort(
    (a, b) =>
      rank(a.label) - rank(b.label) ||
      a.label.localeCompare(b.label) ||
      a.badge.localeCompare(b.badge),
  );
  if (selected)
    return (
      <div className="flex flex-col gap-5">
        {!started && (
          <Button
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={() => setSelected(undefined)}
          >
            <ArrowLeftIcon aria-hidden="true" />
            {t("Choose another source")}
          </Button>
        )}
        <div className="flex items-center gap-3">
          {selected.kind === "connector" ? (
            <BrandIcon
              alias={selected.connector.key}
              logo={selected.connector.logo_url}
            />
          ) : (
            <BrandIcon
              identity={selected.preset?.id}
              logo={selected.preset?.logo}
              endpoint={selected.preset?.endpoint}
            />
          )}
          <div>
            <h3 className="font-medium">
              {selected.kind === "connector"
                ? selected.connector.name
                : (selected.preset?.name ?? t("Custom Remote MCP"))}
            </h3>
            <p className="text-sm text-muted-foreground">
              {selected.kind === "connector"
                ? selected.provider.name
                : t("Remote MCP")}
            </p>
          </div>
        </div>
        {selected.kind === "connector" ? (
          <>
            <ConnectionSetup
              connector={selected.connector}
              onStarted={() => setStarted(true)}
            />
            {selected.provider.type === "composio" && (
              <div className="space-y-3 text-sm text-muted-foreground">
                {selected.connector.authentication_methods.includes(
                  "OAUTH2",
                ) && (
                  <DisclosureSection title={t("Use your own OAuth app")}>
                    <ol className="list-decimal space-y-2 pl-5">
                      <li>
                        {t(
                          "In Composio Dashboard, create an auth config for this application and select custom credentials.",
                        )}
                      </li>
                      <li>
                        {t(
                          "Enter your client ID, client secret and scopes there. Register the redirect URI shown by Composio with your OAuth app.",
                        )}
                      </li>
                      <li>
                        {t(
                          "Return here, refresh configurations, and select your new config.",
                        )}
                      </li>
                    </ol>
                  </DisclosureSection>
                )}
                <a
                  href="https://dashboard.composio.dev"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("Manage auth configs in Composio Dashboard")}
                </a>
              </div>
            )}
            <ConnectorToolPreview connector={selected.connector} />
          </>
        ) : (
          <CreateMCP
            onCancel={onCancel}
            preset={selected.preset}
            onStarted={() => setStarted(true)}
            onSuccess={(connection) => onConnected(connection.id)}
          />
        )}
      </div>
    );
  return (
    <div className="flex flex-col gap-4">
      <FormField label={t("Source")}>
        <SearchPicker
          label={t("Source")}
          placeholder={t("Search sources…")}
          emptyMessage={
            directory.pending ? t("Loading sources…") : t("No matching sources")
          }
          groups={[{ label: "", options }]}
          onSearchChange={setSearch}
          onValueChange={(value) => {
            const connector = directory.entries.find(
              (entry) =>
                value ===
                `connector:${entry.provider.id}:${entry.connector.key}`,
            );
            if (connector) {
              setSelected({ kind: "connector", ...connector });
              return;
            }
            const preset = mcpPresets.find(
              (entry) => value === `mcp:${entry.id}`,
            );
            if (preset) setSelected({ kind: "mcp", preset });
          }}
          footer={
            directory.pending ? (
              <p
                role="status"
                className="px-2 py-1 text-sm text-muted-foreground"
              >
                {t("Loading sources…")}
              </p>
            ) : directory.hasMore ? (
              <Button
                className="w-full"
                variant="ghost"
                size="sm"
                loading={directory.loadingMore}
                onClick={directory.loadMore}
              >
                {t("Load more applications")}
              </Button>
            ) : undefined
          }
        />
      </FormField>
      <ErrorNotice error={directory.error} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <ManageProvidersLink category="connectors" scope="workspace" />
          {!!directory.providers.length && (
            <Button
              variant="ghost"
              size="sm"
              loading={directory.loadingMore}
              onClick={directory.refresh}
              type="button"
            >
              {t("Refresh applications")}
            </Button>
          )}
        </div>
        {can("connection.manage") && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => setSelected({ kind: "mcp" })}
          >
            <PlusIcon />
            {t("Custom Remote MCP")}
          </Button>
        )}
      </div>
    </div>
  );
}
