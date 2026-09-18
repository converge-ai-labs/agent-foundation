import { useState } from "react";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty } from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { AgentLink } from "../agents/link";
import { runPath } from "../conversations/api";
import styles from "./bots.module.css";
import { BotReplies } from "./replies";

export function BotConversations({
  accountId,
  targetId,
}: {
  accountId: string;
  targetId?: string;
}) {
  // Remount on scope changes so an old scope's pagination cannot leak into a new query.
  return (
    <ConversationList
      key={`${accountId}:${targetId ?? ""}`}
      accountId={accountId}
      targetId={targetId}
    />
  );
}

function ConversationList({
  accountId,
  targetId,
}: {
  accountId: string;
  targetId?: string;
}) {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const page = useCursor();
  const [replyRun, setReplyRun] = useState<string>();
  const query = useQuery({
    queryKey: ["bot-threads", workspace.id, accountId, targetId, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/threads", {
          params: {
            path: { account_id: accountId },
            query: { target_id: targetId, cursor: page.cursor, limit: 20 },
          },
          signal,
        })
        .then(data),
  });
  return (
    <section
      className={styles.memorySection}
      aria-label={t("Bot conversations")}
    >
      <div className={styles.memoryHeading}>
        <div>
          <h2>{t("Conversations")}</h2>
          <p>
            {t(
              "Conversations started by this bot, within your existing history permissions.",
            )}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {t("Refresh")}
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items.map((item) => ({
              ...item,
              id: item.binding_id,
            }))}
            columns={[
              {
                label: t("Conversation"),
                tone: "primary",
                render: (item) => (
                  <Link
                    className={styles.historyIdentity}
                    title={item.thread_id}
                    to={runPath(basePath, item)}
                  >
                    {item.thread_id}
                  </Link>
                ),
              },
              ...(can("application_account.read")
                ? [
                    {
                      label: t("Replies"),
                      render: (item: { run_id: string }) => (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setReplyRun(item.run_id)}
                        >
                          {t("Inspect replies")}
                        </Button>
                      ),
                    },
                  ]
                : []),
              {
                label: t("Agent"),
                render: (item) => <AgentLink agentId={item.agent_id} />,
              },
              {
                label: t("Run status"),
                render: (item) => <StatePill state={item.run_status} />,
              },
              {
                label: t("Updated"),
                render: (item) => <Timestamp value={item.updated_at} />,
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No visible bot conversations")}
            description={t(
              "Accepted conversations appear here when you have permission to view their sessions, threads, and runs.",
            )}
          />
        )
      )}
      {replyRun && (
        <>
          <Button variant="ghost" onClick={() => setReplyRun(undefined)}>
            {t("Close reply observations")}
          </Button>
          <BotReplies accountId={accountId} runId={replyRun} />
        </>
      )}
      <p className={styles.historyNote}>
        {t(
          "Run status describes agent execution. It does not confirm that a reply was delivered to the platform.",
        )}
      </p>
    </section>
  );
}
