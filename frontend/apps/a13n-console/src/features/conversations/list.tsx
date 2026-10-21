import { BrandIcon, Button, MenuItem } from "a13n-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowRightIcon,
  ArrowUpRightIcon,
  ChatsIcon,
  CopyIcon,
  PencilSimpleIcon,
  PlusIcon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import type { Schema } from "../../shared/api";
import { Page } from "../../shared/page";
import { AgentAvatar } from "../agents/avatar";
import { conversationQueries, type SessionFilters } from "./api";
import {
  SessionFilterBar,
  readSessionFilters,
  useSessionAgents,
} from "./filters";
import { originLink, sessionPath, sessionTitle } from "./session-identity";
import { SessionEditor } from "./session-editor";
import styles from "./session-list.module.css";

const REFRESH_MS = 15_000;

export function SessionList() {
  const { t } = useTranslation(),
    { can, basePath } = useWorkspace();
  const [search, setSearch] = useSearchParams();
  const filters = readSessionFilters(search);
  const create = can("run") && (
    <Button render={<Link to={`${basePath}/sessions/new`} />}>
      <PlusIcon aria-hidden="true" />
      {t("New session")}
    </Button>
  );
  return (
    <Page
      title={t("Sessions")}
      description={t(
        "Find a conversation, follow its progress, or trace it back to the source.",
      )}
      actions={create}
      toolbar={<SessionFilterBar search={search} setSearch={setSearch} />}
    >
      <SessionResults
        filters={filters}
        create={create}
        onFilter={(key, value) =>
          setSearch((previous) => {
            const next = new URLSearchParams(previous);
            if (key === "label") {
              if (!next.getAll(key).includes(value)) next.append(key, value);
            } else next.set(key, value);
            return next;
          })
        }
      />
    </Page>
  );
}

function SessionResults({
  filters,
  create,
  onFilter,
}: {
  filters: SessionFilters;
  create: ReactNode;
  onFilter: (key: "application" | "label", value: string) => void;
}) {
  const { t } = useTranslation(),
    { workspace, basePath, can } = useWorkspace(),
    navigate = useNavigate(),
    client = useClient(),
    page = useCursor(filters);
  const [editing, setEditing] = useState<Schema["SessionView"]>();
  const sessions = useQuery({
    ...conversationQueries(client, workspace.id).sessions(page.cursor, filters),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH_MS,
  });
  const agents = useSessionAgents();
  if (sessions.isPending) return <Loading variant="table" columns={4} />;
  if (!sessions.data)
    return (
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
    );
  const items = sessions.data.items;
  const agentImages = new Map(
    agents.data?.map((agent) => [agent.id, agent.image_url]),
  );
  const path = (id: string) => sessionPath(basePath, id, filters.q);
  const filtered = Object.values(filters).some((value) =>
    Array.isArray(value) ? value.length > 0 : !!value,
  );
  return (
    <>
      <ErrorNotice error={sessions.error} />
      {items.length ? (
        <div className={styles.collection}>
          <ResourceTable
            items={items}
            caption={t("Sessions")}
            className={styles.table}
            onRowActivate={(session) =>
              navigate(`${path(session.id)}?view=debug`)
            }
            columns={[
              {
                label: t("Session"),
                tone: "primary",
                dataColumn: "identity",
                render: (session) => (
                  <SessionIdentity
                    session={session}
                    agentImage={
                      session.preview &&
                      agentImages.get(session.preview.agent_id)
                    }
                    href={`${path(session.id)}?view=debug`}
                    onFilter={onFilter}
                  />
                ),
              },
              {
                label: t("Source"),
                dataColumn: "source",
                render: (session) => (
                  <SessionSource session={session} onFilter={onFilter} />
                ),
              },
              {
                label: t("Status"),
                dataColumn: "state",
                render: (session) => (
                  <div
                    className={styles.state}
                    title={
                      session.preview
                        ? t(`trigger.${session.preview.trigger}`, {
                            defaultValue: session.preview.trigger,
                          })
                        : undefined
                    }
                  >
                    {session.preview ? (
                      <StatePill state={session.preview.status} />
                    ) : (
                      <span className={styles.unspecified}>
                        {t("Not started")}
                      </span>
                    )}
                  </div>
                ),
              },
              {
                label: t("Runs"),
                dataColumn: "runs",
                align: "right",
                render: (session) => (
                  <span className={styles.runCount}>
                    {session.run_count ?? "—"}
                  </span>
                ),
              },
              {
                label: t("Updated"),
                dataColumn: "activity",
                render: (session) => (
                  <span className={styles.activity}>
                    <Timestamp value={session.updated_at} relative />
                  </span>
                ),
              },
            ]}
            rowMenuLabel={t("Session actions")}
            rowMenu={(session) => (
              <>
                <MenuItem
                  render={<Link to={`${path(session.id)}?view=debug`} />}
                >
                  <ArrowRightIcon aria-hidden="true" />
                  {t("Inspect execution")}
                </MenuItem>
                {can("run") && (
                  <MenuItem
                    render={<Link to={`${path(session.id)}?view=chat`} />}
                  >
                    <ChatsIcon aria-hidden="true" />
                    {t("Continue thread")}
                  </MenuItem>
                )}
                {can("run") && (
                  <MenuItem onClick={() => setEditing(session)}>
                    <PencilSimpleIcon aria-hidden="true" />
                    {t("Edit session details")}
                  </MenuItem>
                )}
                <MenuItem
                  onClick={() =>
                    void navigator.clipboard?.writeText(session.id)
                  }
                >
                  <CopyIcon aria-hidden="true" />
                  {t("Copy session ID")}
                </MenuItem>
              </>
            )}
          />
        </div>
      ) : (
        <Empty
          icon={<ChatsIcon aria-hidden="true" />}
          title={t(filtered ? "No matching sessions" : "No sessions yet")}
          description={t(
            filtered
              ? "Try a title, source link, external ID, or words from the conversation."
              : "Start a session to talk with an agent in this workspace.",
          )}
          action={!filtered && create}
        />
      )}
      <CollectionFooter
        count={
          items.length
            ? t("{{count}} sessions on this page", { count: items.length })
            : undefined
        }
      >
        <Pagination page={page} next={sessions.data.next_cursor} />
      </CollectionFooter>
      {editing && (
        <SessionEditor
          key={editing.id}
          session={editing}
          onClose={() => setEditing(undefined)}
        />
      )}
    </>
  );
}

const VISIBLE_LABELS = 2;

/** The latest Run's Agent is the row's face; labels narrow the list in place. */
function SessionIdentity({
  session,
  agentImage,
  href,
  onFilter,
}: {
  session: Schema["SessionView"];
  agentImage?: string | null;
  href: string;
  onFilter: (key: "application" | "label", value: string) => void;
}) {
  const { t } = useTranslation();
  const title = sessionTitle(session);
  const agent = session.preview;
  const labels = Object.entries(session.labels).filter(
    ([key]) => !key.startsWith("a13n."),
  );
  const hidden = labels.slice(VISIBLE_LABELS);
  return (
    <div className={styles.identity}>
      {agent ? (
        <AgentAvatar
          name={agent.agent_name}
          id={agent.agent_id}
          url={agentImage}
          className={styles.avatar}
        />
      ) : (
        <span className={styles.avatar} aria-hidden="true">
          <ChatsIcon size={16} />
        </span>
      )}
      <div className={styles.identityText}>
        <Link
          className={styles.title}
          data-untitled={!title || undefined}
          to={href}
          title={title ?? undefined}
        >
          {title ?? t("Untitled session")}
        </Link>
        <div className={styles.metadata}>
          {!title && <span className={styles.sessionId}>{session.id}</span>}
          {agent && (
            <span className={styles.agent} title={agent.agent_name}>
              {agent.agent_name}
            </span>
          )}
          {labels.length > 0 && (
            <span className={styles.tags}>
              {labels.slice(0, VISIBLE_LABELS).map(([key, value]) => (
                <button
                  key={key}
                  type="button"
                  className={styles.tag}
                  title={`${key}: ${value}`}
                  aria-label={t("Filter by {{label}}", {
                    label: `${key}: ${value}`,
                  })}
                  onClick={() => onFilter("label", `${key}:${value}`)}
                >
                  {value ? (
                    <>
                      <span className={styles.tagKey}>{key}</span> {value}
                    </>
                  ) : (
                    key
                  )}
                </button>
              ))}
              {hidden.length > 0 && (
                <span
                  className={styles.tag}
                  title={hidden
                    .map(([key, value]) => `${key}: ${value}`)
                    .join("\n")}
                >
                  +{hidden.length}
                </span>
              )}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** The named conversation leads; its application is the caption that filters. */
function SessionSource({
  session,
  onFilter,
}: {
  session: Schema["SessionView"];
  onFilter: (key: "application", value: string) => void;
}) {
  const { t } = useTranslation();
  const origin = session.origin;
  if (!origin)
    return (
      <span className={styles.unspecified} title={t("Not specified")}>
        <span aria-hidden="true">—</span>
        <span className="sr-only">{t("Not specified")}</span>
      </span>
    );
  const link = originLink(origin);
  const name = origin.name || (link && new URL(link).hostname);
  return (
    <div className={styles.source}>
      <span className={styles.sourceMark} aria-hidden="true">
        <BrandIcon alias={origin.application} endpoint={link} size={14} />
      </span>
      <div className={styles.sourceText}>
        {link ? (
          <a
            className={styles.sourceName}
            href={link}
            target="_blank"
            rel="noreferrer"
            title={origin.name || link}
          >
            <span>{name}</span>
            <ArrowUpRightIcon size={11} aria-hidden="true" />
          </a>
        ) : name ? (
          <span className={styles.sourceName} title={name}>
            <span>{name}</span>
          </span>
        ) : null}
        <button
          type="button"
          className={styles.application}
          data-lead={!name || undefined}
          onClick={() => onFilter("application", origin.application)}
          title={t("Filter by {{label}}", { label: origin.application })}
        >
          {origin.application}
        </button>
      </div>
    </div>
  );
}
