import { useState } from "react";
import { ArrowClockwiseIcon, ChatsCircleIcon } from "@phosphor-icons/react";
import { Button, MenuItem } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
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
import { Panel, Section } from "../../shared/page";
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
    { t } = useTranslation(),
    navigate = useNavigate();
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
  const items =
    query.data?.items.map((item) => ({ ...item, id: item.binding_id })) ?? [];
  return (
    <Section
      title={t("Conversations")}
      description={t(
        "Conversations started by this bot, within your existing history permissions.",
      )}
      actions={
        <Button
          size="sm"
          variant="ghost"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <ArrowClockwiseIcon aria-hidden="true" />
          {t("Refresh")}
        </Button>
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Conversations")}
            onRowActivate={(item) => navigate(runPath(basePath, item))}
            rowMenuLabel={t("Conversation actions")}
            rowMenu={(item) =>
              can("application_account.read") ? (
                <MenuItem onClick={() => setReplyRun(item.run_id)}>
                  {t("Inspect replies")}
                </MenuItem>
              ) : null
            }
            columns={[
              {
                label: t("Conversation"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    to={runPath(basePath, item)}
                    name={item.thread_id}
                    description={t("Thread")}
                    icon={<ChatsCircleIcon aria-hidden="true" size={16} />}
                    resourceId={item.run_id}
                  />
                ),
              },
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
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} conversations on this page", {
              count: items.length,
            })}
          >
            <Pagination page={page} next={query.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<ChatsCircleIcon aria-hidden="true" />}
            title={t("No visible bot conversations")}
            description={t(
              "Accepted conversations appear here when you have permission to view their sessions, threads, and runs.",
            )}
          />
        )
      )}
      <p className={styles.hint}>
        {t(
          "Run status describes agent execution. It does not confirm that a reply was delivered to the platform.",
        )}
      </p>
      <Panel
        open={!!replyRun}
        title={t("Platform reply observations")}
        onClose={() => setReplyRun(undefined)}
      >
        {replyRun && <BotReplies accountId={accountId} runId={replyRun} />}
      </Panel>
    </Section>
  );
}
