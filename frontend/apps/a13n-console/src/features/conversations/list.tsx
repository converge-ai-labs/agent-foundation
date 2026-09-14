import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  Timestamp,
  StateBadge,
} from "../../shared/feedback";
import { CopyableId } from "../../shared/copy";
import { conversationQueries, type SessionFilters } from "./api";
import { SessionFilterBar, readSessionFilters } from "./filters";
import styles from "./conversations.module.css";

export function SessionList({
  notificationError,
  reconnect,
}: {
  notificationError: unknown;
  reconnect: () => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const filters = readSessionFilters(search);
  return (
    <Page
      title={t("Sessions")}
      description={t("Review conversations and runs across your workspace.")}
    >
      <SessionFilterBar search={search} setSearch={setSearch} />
      <ErrorNotice error={notificationError} retry={reconnect} />
      <SessionResults key={search.toString()} filters={filters} />
    </Page>
  );
}

function SessionResults({ filters }: { filters: SessionFilters }) {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    navigate = useNavigate(),
    client = useClient(),
    page = useCursor();
  const sessions = useQuery(
    conversationQueries(client, workspace.id).sessions(page.cursor, filters),
  );
  return (
    <>
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
      {sessions.isPending ? (
        <Loading variant="table" columns={7} />
      ) : sessions.data?.items.length ? (
        <ResourceTable
          items={sessions.data.items}
          caption={t("Sessions")}
          onRowActivate={(session) =>
            navigate(
              `${basePath}/sessions/${session.id}${filters.q && filters.q !== session.id ? `/threads/${encodeURIComponent(filters.q)}` : ""}`,
            )
          }
          columns={[
            {
              label: t("Session ID"),
              tone: "muted",
              render: (session) => (
                <div className={styles.sessionId} title={session.id}>
                  <CopyableId value={session.id} />
                </div>
              ),
            },
            {
              label: t("Request summary"),
              tone: "primary",
              render: (session) => (
                <span
                  className={styles.sessionSummary}
                  data-empty={!session.preview?.input_text || undefined}
                  title={session.preview?.input_text || t("No request text")}
                >
                  {session.preview?.input_text || t("No request text")}
                </span>
              ),
            },
            {
              label: t("Recent agent"),
              render: (session) => (
                <span
                  className={styles.sessionAgent}
                  title={session.preview?.agent_name ?? undefined}
                >
                  {session.preview?.agent_name ?? "—"}
                </span>
              ),
            },
            {
              label: t("Recent run status"),
              render: (session) =>
                session.preview ? (
                  <StateBadge state={session.preview.run_status} />
                ) : (
                  "—"
                ),
            },
            {
              label: t("Runs"),
              align: "right",
              render: (session) => session.run_count ?? "—",
            },
            {
              label: t("Trigger source"),
              tone: "muted",
              render: (session) =>
                session.preview
                  ? t(`trigger.${session.preview.trigger_type}`, {
                      defaultValue: session.preview.trigger_type,
                    })
                  : "—",
            },
            {
              label: t("Last updated"),
              tone: "muted",
              render: (session) => (
                <Timestamp value={session.updated_at} relative />
              ),
            },
          ]}
        />
      ) : (
        !sessions.error && (
          <Empty
            title={t("No matching sessions")}
            description={t("Change or clear the search and filters.")}
          />
        )
      )}
      {sessions.data && (
        <Pagination page={page} next={sessions.data.next_cursor} />
      )}
    </>
  );
}
