import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  DisclosureSection,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  ReadOnlyField,
} from "a13n-ui";
import {
  ChatIcon,
  CheckCircleIcon,
  DotsThreeOutlineVerticalIcon,
  DownloadSimpleIcon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, ifMatch, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { JsonView, TextAreaField } from "../../shared/forms";
import { Panel } from "../../shared/page";
import { workspaceModelsQuery } from "../models/api";
import { ModelKeyLabel } from "../models/model-icon";
import { useAgentComposer } from "./composer";
import { ExportAgent } from "./export";
import { editableAgent } from "./queries";
import shared from "../../shared/shared.module.css";
import styles from "./agents.module.css";

type Revision = Schema["AgentRevision"];

/** A version as every history names it: its number, and whether it is the default. */
function VersionName({
  number,
  isDefault,
}: {
  number: number;
  isDefault: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span className={styles.versionName}>
      <span>v{number}</span>
      {isDefault && <Badge variant="secondary">{t("Default")}</Badge>}
    </span>
  );
}

export function AgentVersions({
  agent,
  etag,
  onDefaultChanged,
}: {
  agent: Schema["Agent"];
  etag?: string;
  onDefaultChanged: () => Promise<void>;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace } = useWorkspace(),
    page = useCursor(),
    composer = useAgentComposer(),
    author = useAuthors();
  const [selected, setSelected] = useState<Revision>();
  const query = useQuery({
    queryKey: ["agent-revisions", workspace.id, agent.id, page.cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents/{agent_id}/revisions", {
          params: {
            path: { agent_id: agent.id },
            query: { cursor: page.cursor, limit: 20 },
          },
          signal,
        })
        .then(data),
  });
  // Model names resolve with the versions, so no row shows a bare key first.
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  if (query.isPending || models.isPending)
    return <Loading variant="table" columns={5} rows={5} />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  const actions = (revision: Revision) => (
    <VersionActions
      agent={agent}
      etag={etag}
      revision={revision}
      composer={composer}
      onDefaultChanged={onDefaultChanged}
    />
  );
  return (
    <div className={shared.stack}>
      <p className={shared.muted}>
        {t(
          "Versions are immutable. Setting the default does not create a version.",
        )}
      </p>
      <VersionTable
        items={query.data.items}
        defaultId={agent.default_revision_id}
        content={{
          label: t("Model"),
          render: (item) => (
            <ModelKeyLabel
              modelKey={item.config.model}
              size={16}
              className={styles.modelName}
            />
          ),
        }}
        onRowActivate={setSelected}
        rowMenu={actions}
      />
      <Pagination page={page} next={query.data.next_cursor} />
      {composer.setup}
      <ErrorToast error={composer.error} />
      <Panel
        open={!!selected}
        onClose={() => setSelected(undefined)}
        label={t("Version details")}
        title={
          selected && (
            <VersionName
              number={selected.number}
              isDefault={selected.id === agent.default_revision_id}
            />
          )
        }
        actions={
          selected && (
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    type="button"
                    aria-label={t("Version actions")}
                  />
                }
              >
                <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
              </MenuTrigger>
              <MenuPopup align="end">{actions(selected)}</MenuPopup>
            </Menu>
          )
        }
      >
        {selected && (
          <div className={styles.versionDetail}>
            <ReadOnlyField label={t("Model")}>
              <ModelKeyLabel
                modelKey={selected.config.model}
                size={16}
                className={styles.modelName}
              />
            </ReadOnlyField>
            <div className={styles.versionFacts}>
              <ReadOnlyField label={t("Created")}>
                <Timestamp value={selected.created_at} />
              </ReadOnlyField>
              <ReadOnlyField label={t("Created by")}>
                {author(selected.created_by_id)}
              </ReadOnlyField>
            </div>
            <ReadOnlyField label={t("Version note")}>
              {selected.note || "—"}
            </ReadOnlyField>
            <TextAreaField
              readOnly
              label={t("Instructions")}
              value={selected.config.instructions ?? ""}
              onChange={() => {}}
            />
            <DisclosureSection title={t("Configuration details")}>
              <JsonView value={selected.config} />
            </DisclosureSection>
          </div>
        )}
      </Panel>
    </div>
  );
}

type Version = Pick<
  Revision,
  "id" | "number" | "note" | "created_at" | "created_by_id"
>;

/**
 * Every version history, agent or skill: the number, what the version holds,
 * its note, and who created it when. Actions live in each row's menu.
 */
export function VersionTable<T extends Version>({
  items,
  defaultId,
  content,
  onRowActivate,
  rowMenu,
}: {
  items: readonly T[];
  defaultId?: string | null;
  /** The column that says what this kind of version holds. */
  content: { label: string; render: (item: T) => ReactNode };
  onRowActivate: (item: T) => void;
  rowMenu: (item: T) => ReactNode;
}) {
  const { t } = useTranslation(),
    author = useAuthors();
  return (
    <ResourceTable
      className={styles.versionTable}
      items={items}
      caption={t("Versions")}
      onRowActivate={onRowActivate}
      rowMenuLabel={t("Version actions")}
      rowMenu={rowMenu}
      columns={[
        {
          label: t("Version"),
          tone: "primary",
          render: (item) => (
            <VersionName
              number={item.number}
              isDefault={item.id === defaultId}
            />
          ),
        },
        content,
        {
          label: t("Version note"),
          render: (item) => (
            <span className={styles.versionNote} title={item.note ?? undefined}>
              {item.note || "—"}
            </span>
          ),
        },
        {
          label: t("Created"),
          tone: "muted",
          render: (item) => <Timestamp value={item.created_at} />,
        },
        {
          label: t("Created by"),
          tone: "muted",
          render: (item) => author(item.created_by_id),
        },
      ]}
    />
  );
}

/** The same actions open from a version's row and from its details. */
function VersionActions({
  agent,
  etag,
  revision,
  composer,
  onDefaultChanged,
}: {
  agent: Schema["Agent"];
  etag?: string;
  revision: Revision;
  composer: ReturnType<typeof useAgentComposer>;
  onDefaultChanged: () => Promise<void>;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient();
  return (
    <>
      {can("write") &&
        editableAgent(agent) &&
        revision.id !== agent.default_revision_id && (
          <Confirm
            subject={`${agent.name} · v${revision.number}`}
            title={t("Set as default")}
            description={t(
              "Future runs will use this version unless another version is selected.",
            )}
            triggerElement={
              <MenuItem closeOnClick={false}>
                <CheckCircleIcon aria-hidden="true" />
                {t("Set as default")}
              </MenuItem>
            }
            action={async () => {
              if (!etag)
                throw new Error(
                  t("Version information is unavailable. Reload this page."),
                );
              await client
                .workspace(workspace.id)
                .POST(
                  "/api/v1/agents/{agent_id}/revisions/{revision_id}/set-default",
                  {
                    params: {
                      path: { agent_id: agent.id, revision_id: revision.id },
                    },
                    headers: ifMatch(etag),
                  },
                )
                .then(data);
              await onDefaultChanged();
              await cache.invalidateQueries({
                queryKey: ["agent-revisions", workspace.id, agent.id],
              });
            }}
          />
        )}
      {composer.available && (
        <MenuItem
          disabled={composer.pending}
          onClick={() => composer.start({ agent, revision })}
        >
          <ChatIcon aria-hidden="true" />
          {t("Configure from this version")}
        </MenuItem>
      )}
      <ExportAgent
        agent={agent}
        config={revision.config}
        version={revision.number}
        trigger={
          <MenuItem closeOnClick={false}>
            <DownloadSimpleIcon aria-hidden="true" />
            {t("Export version")}
          </MenuItem>
        }
      />
    </>
  );
}

/**
 * Versions name their author by ID. The reader is always known; other members
 * resolve through the organization's member list, which only administrators
 * may read, and otherwise stay a short reference.
 */
function useAuthors() {
  const client = useClient(),
    { t } = useTranslation(),
    { organization, organizationCan } = useWorkspace(),
    me = useAuth().data?.user.value;
  const members = useQuery({
    queryKey: ["organization-members", organization.id],
    enabled: organizationCan("admin"),
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization_id}/members", {
            params: {
              path: { organization_id: organization.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  return (id: string) => {
    const person =
      id === me?.id ? me : members.data?.find((member) => member.id === id);
    return (
      <span className={styles.author} title={person?.email ?? id}>
        {person && (
          <UserAvatar
            name={person.name}
            id={person.id}
            url={person.image_url}
            className="size-5 text-[9px]"
          />
        )}
        <span>
          {person
            ? person.name
            : t("Member · {{reference}}", { reference: id.slice(-8) })}
        </span>
      </span>
    );
  };
}
