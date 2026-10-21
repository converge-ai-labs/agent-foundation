import { Button, MenuItem } from "a13n-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowRightIcon,
  ArrowUpRightIcon,
  ChatsIcon,
  CopyIcon,
  LinkSimpleIcon,
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
import { conversationQueries, type SessionFilters } from "./api";
import { SessionFilterBar, readSessionFilters } from "./filters";
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
  if (sessions.isPending) return <Loading variant="table" columns={4} />;
  if (!sessions.data)
    return (
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
    );
  const items = sessions.data.items;
  const path = (id: string) => sessionPath(basePath, id, filters.q);
  const filtered = Object.values(filters).some((value) =>
    Array.isArray(value) ? value.length > 0 : !!value,
  );
  return (
    <>
      <ErrorNotice error={sessions.error} />
      {items.length ? (
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
              label: t("Latest run"),
              dataColumn: "state",
              render: (session) => (
                <div className={styles.state}>
                  {session.preview ? (
                    <>
                      <StatePill state={session.preview.status} />
                      <span>
                        {t(`trigger.${session.preview.trigger}`, {
                          defaultValue: session.preview.trigger,
                        })}
                      </span>
                    </>
                  ) : (
                    <span>{t("Not started")}</span>
                  )}
                </div>
              ),
            },
            {
              label: t("Activity"),
              dataColumn: "activity",
              align: "right",
              render: (session) => (
                <div className={styles.activity}>
                  <Timestamp value={session.updated_at} relative />
                  <span>
                    {session.run_count == null
                      ? "—"
                      : t("{{count}} runs", { count: session.run_count })}
                  </span>
                </div>
              ),
            },
            {
              label: t("Inspect execution"),
              header: <span className="sr-only">{t("Inspect execution")}</span>,
              dataColumn: "open",
              render: (session) => (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("Inspect execution")}
                  title={t("Inspect execution")}
                  render={<Link to={`${path(session.id)}?view=debug`} />}
                >
                  <ArrowRightIcon size={16} />
                </Button>
              ),
            },
          ]}
          rowMenuLabel={t("Session actions")}
          rowMenu={(session) => (
            <>
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
                onClick={() => void navigator.clipboard?.writeText(session.id)}
              >
                <CopyIcon aria-hidden="true" />
                {t("Copy session ID")}
              </MenuItem>
            </>
          )}
        />
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

function SessionIdentity({
  session,
  href,
  onFilter,
}: {
  session: Schema["SessionView"];
  href: string;
  onFilter: (key: "application" | "label", value: string) => void;
}) {
  const { t } = useTranslation();
  const title = sessionTitle(session) ?? t("Untitled session");
  const labels = Object.entries(session.labels).filter(
    ([key]) => !key.startsWith("a13n."),
  );
  const output = session.preview?.output_text?.replace(/\s+/g, " ").trim();
  return (
    <div className={styles.identity}>
      <span className={styles.avatar} aria-hidden="true">
        {session.origin ? (
          <LinkSimpleIcon size={18} />
        ) : (
          <ChatsIcon size={18} />
        )}
      </span>
      <div className={styles.identityText}>
        <Link className={styles.title} to={href} title={title}>
          {title}
        </Link>
        {output && (
          <p className={styles.preview} title={output}>
            {output}
          </p>
        )}
        <div className={styles.metadata}>
          {!session.title && (
            <span className={styles.sessionId}>{session.id}</span>
          )}
          {session.preview?.agent_name && (
            <span className={styles.agent}>{session.preview.agent_name}</span>
          )}
          {labels.slice(0, 3).map(([key, value]) => (
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
              {value || key}
            </button>
          ))}
          {labels.length > 3 && (
            <span
              title={labels
                .slice(3)
                .map(([key, value]) => `${key}: ${value}`)
                .join(" · ")}
            >
              +{labels.length - 3}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

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
    return <span className={styles.unspecified}>{t("Not specified")}</span>;
  const link = originLink(origin);
  return (
    <div className={styles.source}>
      <button
        type="button"
        className={styles.application}
        onClick={() => onFilter("application", origin.application)}
        title={t("Filter by {{label}}", { label: origin.application })}
      >
        {origin.application}
      </button>
      {link ? (
        <a
          className={styles.sourceLink}
          href={link}
          target="_blank"
          rel="noreferrer"
          title={origin.name || link}
        >
          <span>{origin.name || new URL(link).hostname}</span>
          <ArrowUpRightIcon size={12} aria-hidden="true" />
        </a>
      ) : origin.name ? (
        <span className={styles.sourceName} title={origin.name}>
          {origin.name}
        </span>
      ) : null}
      {origin.external_id && (
        <span className={styles.externalId} title={origin.external_id}>
          {origin.external_id}
        </span>
      )}
    </div>
  );
}
