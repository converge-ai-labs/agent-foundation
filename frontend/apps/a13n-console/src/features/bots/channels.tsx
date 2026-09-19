import {
  ChatCircleDotsIcon,
  GitBranchIcon,
  PencilSimpleIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { MenuItem } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { AgentLink } from "../agents/link";
import { TargetEditor } from "../application-accounts/targets";
import { ReceptionPill } from "../integrations/platform";
import type { BotAccount } from "./account";

/**
 * Conversations carry a platform name; the external identifier is evidence,
 * not a title. Names come from the memory scopes the bot already tracks.
 */
export function useChannelNames(account: BotAccount) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const providerId = account.memory?.provider_id;
  const query = useQuery({
    queryKey: ["bot-channel-names", workspace.id, account.id, providerId],
    enabled: !!providerId,
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/application-accounts/{account_id}/memory-scopes", {
            params: {
              path: { account_id: account.id },
              query: { provider_id: providerId!, cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const names = new Map(
    (query.data ?? []).map((scope) => [
      scope.external_conversation_id,
      scope.name,
    ]),
  );
  return (externalId: string) => names.get(externalId) || externalId;
}

export function BotChannels({ account }: { account: BotAccount }) {
  const client = useClient(),
    { basePath, can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate(),
    page = useCursor();
  const github = account.provider_key === "github";
  const label = github
    ? "Repositories"
    : account.provider_key === "slack"
      ? "Channels"
      : "Groups";
  const nameOf = useChannelNames(account);
  const query = useQuery({
    queryKey: [
      "account-targets",
      account.workspace_id,
      account.id,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/targets", {
          params: {
            path: { account_id: account.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  const add = can("account_target.manage") && (
    <TargetEditor account={account} bot />
  );
  const items = query.data?.items ?? [];
  return (
    <Section
      title={t(label)}
      description={t(
        "Configure where this bot receives messages and how it responds.",
      )}
      actions={add}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} rows={5} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t(label)}
            onRowActivate={(item) =>
              navigate(`${basePath}/bots/${account.id}/channels/${item.id}`)
            }
            rowMenuLabel={t("Conversation actions")}
            rowMenu={(item) =>
              can("account_target.manage") ? (
                <>
                  <TargetEditor
                    account={account}
                    target={item}
                    bot
                    triggerElement={
                      <MenuItem closeOnClick={false}>
                        <PencilSimpleIcon size={14} />
                        {t("Edit")}
                      </MenuItem>
                    }
                  />
                  <Confirm
                    subject={nameOf(item.external_target_id)}
                    title={t("Delete target override")}
                    description={t(
                      account.reception_scope === "configured_targets"
                        ? "This conversation will no longer be admitted. Existing accepted work is not cancelled."
                        : "The account's default routing will apply to future events for this target.",
                    )}
                    triggerElement={
                      <MenuItem closeOnClick={false} variant="destructive">
                        <TrashIcon size={14} />
                        {t("Delete")}
                      </MenuItem>
                    }
                    danger
                    action={() =>
                      client.http.DELETE(
                        "/api/v1/application-accounts/{account_id}/targets/{target_id}",
                        {
                          params: {
                            path: {
                              account_id: account.id,
                              target_id: item.id,
                            },
                            query: { expected_version: item.version },
                          },
                        },
                      )
                    }
                  />
                </>
              ) : null
            }
            columns={[
              {
                label: t(github ? "Repository" : "Channel"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    to={`${basePath}/bots/${account.id}/channels/${item.id}`}
                    name={nameOf(item.external_target_id)}
                    description={
                      nameOf(item.external_target_id) ===
                      item.external_target_id
                        ? undefined
                        : item.external_target_id
                    }
                    resourceId={item.id}
                    icon={
                      github ? (
                        <GitBranchIcon aria-hidden="true" size={16} />
                      ) : (
                        <ChatCircleDotsIcon aria-hidden="true" size={16} />
                      )
                    }
                  />
                ),
              },
              {
                label: t("Agent"),
                render: (item) =>
                  item.agent_id ? (
                    <AgentLink agentId={item.agent_id} />
                  ) : (
                    t("Account default")
                  ),
              },
              {
                label: t("Reception"),
                render: (item) => (
                  <ReceptionPill enabled={!!item.receive_enabled} />
                ),
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
            icon={<ChatCircleDotsIcon aria-hidden="true" />}
            title={t(
              github
                ? "No repositories configured"
                : "No conversations configured",
            )}
            description={t(
              account.reception_scope === "configured_targets"
                ? "Add a conversation before enabling reception. Unconfigured conversations cannot trigger this bot."
                : "Incoming events use the account defaults unless an exact target overrides them.",
            )}
            action={add}
          />
        )
      )}
    </Section>
  );
}
