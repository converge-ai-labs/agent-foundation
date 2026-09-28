import {
  BrandIcon,
  Button,
  DisclosureSection,
  Input,
  ModalFrame,
} from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import {
  BrandTitle,
  CatalogStep,
  DirectoryEmpty,
  DirectoryGroup,
  DirectoryList,
  DirectoryRow,
} from "../../shared/dialogs";
import { ErrorToast, Loading } from "../../shared/feedback";
import { ConnectionSetup } from "../connectors/setup";
import { ConnectorToolPreview } from "../connectors/tools";
import { CreateMCP } from "../mcp/create";
import { providersPath } from "../providers/navigation";
import { useConnectionDirectory } from "./directory";
import styles from "./connections.module.css";

export type Selection =
  | {
      kind: "connector";
      connector: Schema["ConnectorApp"];
      provider: Schema["Provider"];
    }
  | { kind: "mcp"; preset?: Schema["McpServer"] };

export function sourceName(selected: Selection, t: TFunction) {
  return selected.kind === "connector"
    ? selected.connector.name
    : (selected.preset?.name ?? t("Custom Remote MCP"));
}

export function sourceOrigin(selected: Selection, t: TFunction) {
  return selected.kind === "connector"
    ? selected.provider.name
    : t("Remote MCP");
}

export function SourceIcon({
  selected,
  size,
}: {
  selected: Selection;
  size?: number;
}) {
  return selected.kind === "connector" ? (
    <BrandIcon
      alias={selected.connector.key}
      logo={selected.connector.logo_url}
      size={size}
    />
  ) : (
    <BrandIcon
      identity={selected.preset?.key}
      endpoint={selected.preset?.url}
      logo={selected.preset?.logo_url}
      fallbackIdentity="mcp"
      size={size}
    />
  );
}

/** Setup form for a chosen source: connector authorization or MCP creation. */
export function SourceSetup({
  selected,
  onStarted,
  onCancel,
  onConnected,
}: {
  selected: Selection;
  onStarted: () => void;
  onCancel: () => void;
  onConnected: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (selected.kind === "mcp")
    return (
      <CreateMCP
        onCancel={onCancel}
        preset={selected.preset}
        onStarted={onStarted}
        onSuccess={(connection) => onConnected(connection.id)}
      />
    );
  return (
    <>
      <ConnectionSetup
        connector={selected.connector}
        provider={selected.provider}
        onStarted={onStarted}
      />
      {selected.provider.type === "composio" && (
        <div className={styles.composioHelp}>
          {selected.connector.authentication_methods.includes("OAUTH2") && (
            <DisclosureSection title={t("Use your own OAuth app")}>
              <ol className={styles.steps}>
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
      <ConnectorToolPreview
        connector={selected.connector}
        providerId={selected.provider.id}
      />
    </>
  );
}

/** Catalog-first creation: choose a source, then complete its setup step. */
export function NewConnection({
  onConnected,
}: {
  onConnected: (id: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  return (
    <NewConnectionDialog
      key={generation}
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setGeneration((current) => current + 1);
      }}
      onConnected={(id) => {
        setOpen(false);
        onConnected(id);
      }}
    />
  );
}

function NewConnectionDialog({
  open,
  onOpenChange,
  onConnected,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConnected: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Selection>(),
    [started, setStarted] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      placement="top"
      closeLabel={t("Close")}
      trigger={
        <Button type="button">
          <PlusIcon aria-hidden="true" />
          {t("New connection")}
        </Button>
      }
      title={
        selected ? (
          <BrandTitle mark={<SourceIcon selected={selected} size={22} />}>
            {sourceName(selected, t)}
          </BrandTitle>
        ) : (
          t("New connection")
        )
      }
      description={
        selected
          ? sourceOrigin(selected, t)
          : t(
              "Connect an application or a remote MCP server to this workspace.",
            )
      }
    >
      {open &&
        (selected ? (
          <CatalogStep
            backLabel={t("All sources")}
            onBack={started ? undefined : () => setSelected(undefined)}
          >
            {started && (
              <p className={styles.progressNote} role="status">
                {t("Authorization in progress")}
              </p>
            )}
            <SourceSetup
              selected={selected}
              onStarted={() => setStarted(true)}
              onCancel={() => onOpenChange(false)}
              onConnected={onConnected}
            />
          </CatalogStep>
        ) : (
          <SourceDirectory onSelect={setSelected} />
        ))}
    </ModalFrame>
  );
}

function SourceDirectory({
  onSelect,
}: {
  onSelect: (selection: Selection) => void;
}) {
  const { t } = useTranslation(),
    { can, workspace } = useWorkspace();
  const [search, setSearch] = useState("");
  const directory = useConnectionDirectory(search);
  const manage = can("write");
  const term = search.trim().toLocaleLowerCase();
  const matches = (...values: (string | null | undefined)[]) =>
    !term || values.some((value) => value?.toLocaleLowerCase().includes(term));
  // Name matches outrank description matches; ties keep alphabetical order.
  const rank = (name: string, ...rest: (string | null | undefined)[]) => {
    const lower = name.toLocaleLowerCase();
    return !term || lower.startsWith(term)
      ? 0
      : lower.includes(term)
        ? 1
        : matches(...rest)
          ? 2
          : 3;
  };
  const applications = directory.entries
    .map((entry) => ({
      ...entry,
      rank: rank(
        entry.connector.name,
        entry.connector.key,
        entry.connector.description,
      ),
    }))
    .filter((entry) => entry.rank < 3)
    .sort(
      (a, b) =>
        a.rank - b.rank || a.connector.name.localeCompare(b.connector.name),
    );
  const servers = manage
    ? directory.mcpServers
        .map((preset) => ({
          preset,
          rank: rank(preset.name, preset.key, preset.description),
        }))
        .filter((entry) => entry.rank < 3)
        .sort(
          (a, b) =>
            a.rank - b.rank || a.preset.name.localeCompare(b.preset.name),
        )
        .map((entry) => entry.preset)
    : [];
  const empty = !applications.length && !servers.length;
  return (
    <>
      <DirectoryList
        search={
          <Input
            type="search"
            autoFocus
            aria-label={t("Search applications and MCP servers…")}
            placeholder={t("Search applications and MCP servers…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        }
        footer={
          <>
            <Link to={providersPath("connectors", workspace)}>
              {t("Manage providers")}
            </Link>
            {!!directory.providers.length && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                loading={directory.loadingMore}
                onClick={directory.refresh}
              >
                {t("Refresh applications")}
              </Button>
            )}
          </>
        }
      >
        {manage && !term && (
          <DirectoryRow
            icon={<BrandIcon identity="mcp" />}
            name={t("Custom Remote MCP")}
            detail={t("Connect any compatible remote MCP server.")}
            tone="elevated"
            onClick={() => onSelect({ kind: "mcp" })}
          />
        )}
        {!!applications.length && (
          <DirectoryGroup label={t("Applications")}>
            {applications.map(({ connector, provider }) => (
              <DirectoryRow
                key={`${provider.id}:${connector.key}`}
                icon={
                  <BrandIcon alias={connector.key} logo={connector.logo_url} />
                }
                name={connector.name}
                detail={connector.description}
                meta={provider.name}
                onClick={() =>
                  onSelect({ kind: "connector", connector, provider })
                }
              />
            ))}
          </DirectoryGroup>
        )}
        {!!servers.length && (
          <DirectoryGroup label={t("Remote MCP servers")}>
            {servers.map((preset) => (
              <DirectoryRow
                key={preset.key}
                icon={
                  <BrandIcon
                    identity={preset.key}
                    endpoint={preset.url}
                    logo={preset.logo_url}
                    fallbackIdentity="mcp"
                  />
                }
                name={preset.name}
                detail={preset.description}
                onClick={() => onSelect({ kind: "mcp", preset })}
              />
            ))}
          </DirectoryGroup>
        )}
        {empty &&
          (directory.pending ? (
            <Loading variant="list" rows={4} />
          ) : (
            <DirectoryEmpty>{t("No matching sources")}</DirectoryEmpty>
          ))}
        {directory.hasMore && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={styles.loadMore}
            loading={directory.loadingMore}
            onClick={directory.loadMore}
          >
            {t("Load more applications")}
          </Button>
        )}
      </DirectoryList>
      <ErrorToast error={directory.error} />
    </>
  );
}
