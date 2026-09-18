import { PlugsConnectedIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
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
import { IconTile } from "../../shared/identity";
import {
  DetailHeader,
  DetailLayout,
  DetailPage,
  Page,
  RailRow,
  RailSection,
  Section,
  useTabParam,
} from "../../shared/page";
import {
  PlatformIcon,
  ReceptionPill,
  usePlatformName,
} from "../integrations/platform";
import { AccountActions } from "./actions";
import { accountProviderKey, accountProviderLabel } from "./data";
import { AddApplicationAccount } from "./add-account";
import { AccountCredentials } from "./credentials";
import { AccountTargets } from "./targets";

export function ApplicationAccountsPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    platformName = usePlatformName(),
    page = useCursor(),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["application-accounts", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/application-accounts", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  const add = can("application_account.manage") && (
    <AddApplicationAccount
      onCreated={(account) =>
        navigate(`${basePath}/application-accounts/${account.id}`)
      }
    />
  );
  const items = query.data?.items ?? [];
  return (
    <Page
      title={t("Application accounts")}
      description={t(
        "Bot identities and application installations operated by this workspace.",
      )}
      actions={add}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Application accounts")}
            onRowActivate={(item) =>
              navigate(`${basePath}/application-accounts/${item.id}`)
            }
            columns={[
              {
                label: t("Account"),
                tone: "primary",
                render: (item) => {
                  const organization =
                    item.provider_config?.[
                      item.provider_key === "slack" ? "team_id" : "tenant_key"
                    ];
                  return (
                    <ResourceIdentity
                      to={`${basePath}/application-accounts/${item.id}`}
                      name={item.name}
                      icon={<PlatformIcon type={item.provider_key} />}
                      description={
                        typeof organization === "string" && organization
                          ? `${platformName(item.provider_key)} · ${organization}`
                          : platformName(item.provider_key)
                      }
                      resourceId={item.id}
                    />
                  );
                },
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Reception"),
                render: (item) => (
                  <ReceptionPill enabled={!!item.receive_enabled} />
                ),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    item.credential_configured
                      ? "Configured"
                      : "Not configured",
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
            count={t("{{count}} accounts on this page", {
              count: items.length,
            })}
          >
            <Pagination page={page} next={query.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<PlugsConnectedIcon aria-hidden="true" />}
            title={t("No application accounts")}
            description={t(
              "Add a Slack, Lark, or GitHub application identity using a registered provider.",
            )}
            action={add}
          />
        )
      )}
    </Page>
  );
}

export function ApplicationAccountDetail() {
  const { accountId = "" } = useParams(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    platformName = usePlatformName(),
    [generation, setGeneration] = useState(0),
    [tab, setTab] = useTabParam(["overview", "targets"]),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["application-accounts", workspace.id, accountId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}", {
          params: { path: { account_id: accountId } },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return <ErrorNotice error={query.error} retry={() => void reload()} />;
  const account = query.data;
  const organization =
    account.provider_config?.[
      account.provider_key === "slack" ? "team_id" : "tenant_key"
    ];
  return (
    <DetailPage
      back={`${basePath}/application-accounts`}
      backLabel={t("Application accounts")}
      tab={tab}
      onTabChange={setTab}
      tabs={[
        { value: "overview", label: t("Overview") },
        { value: "targets", label: t("Targets") },
      ]}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <PlatformIcon type={account.provider_key} size={24} />
            </IconTile>
          }
          name={account.name}
          status={<StatePill state={account.status} />}
          description={
            typeof organization === "string" && organization
              ? `${platformName(account.provider_key)} · ${organization}`
              : platformName(account.provider_key)
          }
          actions={
            <AccountActions
              account={account}
              reload={reload}
              onDeleted={() => navigate(`${basePath}/application-accounts`)}
              label={t("Account actions")}
            />
          }
        />
      }
      rail={
        tab === "overview" ? (
          <RailSection title={t("Overview")}>
            <RailRow label={t("Status")}>
              <StatePill state={account.status} />
            </RailRow>
            <RailRow label={t("Reception")}>
              <ReceptionPill enabled={!!account.receive_enabled} />
            </RailRow>
            <RailRow label={t("Provider")}>
              <span
                title={accountProviderKey(
                  account.provider_key,
                  account.provider_config_version,
                )}
              >
                {accountProviderLabel(
                  account.provider_key,
                  account.provider_config_version,
                )}
              </span>
            </RailRow>
            <RailRow label={t("Credentials")}>
              <span>
                {t(
                  account.credential_configured
                    ? "Configured"
                    : "Not configured",
                )}
              </span>
            </RailRow>
            <RailRow label={t("Updated")}>
              <Timestamp value={account.updated_at} relative />
            </RailRow>
          </RailSection>
        ) : undefined
      }
    >
      {tab === "overview" ? (
        <Section
          key={generation}
          title={t("Credentials")}
          description={t(
            "Existing credentials are never displayed. Supply a complete replacement.",
          )}
        >
          <AccountCredentials account={account} reload={reload} />
        </Section>
      ) : (
        <DetailLayout>
          <AccountTargets account={account} />
        </DetailLayout>
      )}
    </DetailPage>
  );
}
