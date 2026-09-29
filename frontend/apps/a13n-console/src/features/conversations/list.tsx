import { Button } from "a13n-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChatsIcon, PlusIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { UNKNOWN } from "../../shared/unknown";
import { Page } from "../../shared/page";
import { AgentAvatar } from "../agents/avatar";
import { conversationQueries, type SessionFilters } from "./api";
import { SessionFilterBar, readSessionFilters } from "./filters";
import styles from "./conversations.module.css";

/** Without workspace notifications, the list refreshes itself this often. */
const REFRESH_MS = 15_000;

export function SessionList() {
  const { t } = useTranslation();
  const { can, basePath } = useWorkspace();
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
      description={t("Every conversation your workspace has run, and why.")}
      actions={create}
      toolbar={<SessionFilterBar search={search} setSearch={setSearch} />}
    >
      <SessionResults filters={filters} create={create} />
    </Page>
  );
}

function SessionResults({
  filters,
  create,
}: {
  filters: SessionFilters;
  create: ReactNode;
}) {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    navigate = useNavigate(),
    client = useClient(),
    page = useCursor(filters);
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
          // The collection opens a session to inspect it: that is the Debug level.
          onRowActivate={(session) =>
            navigate(
              `${basePath}/sessions/${session.id}${filters.q && filters.q !== session.id ? `/threads/${encodeURIComponent(filters.q)}` : ""}?view=debug`,
            )
          }
          columns={[
            {
              label: t("Session"),
              tone: "primary",
              render: (session) => (
                <ResourceIdentity
                  name={session.preview?.input_text || t("No request text")}
                  resourceId={session.id}
                  icon={
                    <AgentAvatar
                      name={session.preview?.agent_name ?? t("Session")}
                      id={session.id}
                      className={styles.sessionAvatar}
                    />
                  }
                  description={[
                    session.preview?.agent_name,
                    session.run_count === null ||
                    session.run_count === undefined
                      ? undefined
                      : t("{{count}} runs", { count: session.run_count }),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
              ),
            },
            {
              label: t("Status"),
              render: (session) =>
                session.preview ? (
                  <StatePill state={session.preview.status} />
                ) : (
                  UNKNOWN
                ),
            },
            {
              label: t("Trigger"),
              tone: "muted",
              render: (session) =>
                session.preview
                  ? t(`trigger.${session.preview.trigger}`, {
                      defaultValue: session.preview.trigger,
                    })
                  : UNKNOWN,
            },
            {
              label: t("Updated"),
              tone: "muted",
              align: "right",
              render: (session) => (
                <Timestamp value={session.updated_at} relative />
              ),
            },
          ]}
        />
      ) : (
        <Empty
          icon={<ChatsIcon aria-hidden="true" />}
          title={t(filtered ? "No matching sessions" : "No sessions yet")}
          description={t(
            filtered
              ? "Change or clear the search and filters."
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
    </>
  );
}
