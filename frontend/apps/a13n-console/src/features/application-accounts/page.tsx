import {
  Button,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { AccountCredentials } from "./credentials";
import { AccountForm } from "./form";

export function ApplicationAccountsPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [open, setOpen] = useState(false),
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
  return (
    <Page
      title={t("Application accounts")}
      description={t(
        "Bot identities and application installations operated by this workspace.",
      )}
      actions={
        can("application_account.manage") && (
          <ModalFrame
            onOpenChange={setOpen}
            trigger={
              <Button variant="default" type="button">
                {t("Add account")}
              </Button>
            }
            size={"lg"}
            title={t("Add application account")}
            description={t(
              "Configure one concrete external identity and its reception settings.",
            )}
            closeLabel={t("Close")}
            open={open}
          >
            {open && (
              <AccountForm
                onSuccess={(account) => {
                  setOpen(false);
                  navigate(account.id);
                }}
              />
            )}
          </ModalFrame>
        )
      }
    >
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Account"),
                render: (item) => (
                  <Link to={item.id}>
                    <strong>{item.name}</strong>
                    <small>{item.provider_key}</small>
                  </Link>
                ),
              },
              {
                label: t("Status"),
                render: (item) => <StateBadge state={item.status} />,
              },
              {
                label: t("Reception"),
                render: (item) => (
                  <StateBadge
                    state={item.receive_enabled ? "enabled" : "disabled"}
                  />
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
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No application accounts")}
            description={t(
              "Add a Slack, Lark, or GitHub application identity using a registered provider.",
            )}
          />
        )
      )}
    </Page>
  );
}
export function ApplicationAccountDetail() {
  const { accountId = "" } = useParams(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    key = useIdempotency(),
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
  if (query.isPending) return <Loading />;
  if (!query.data)
    return <ErrorNotice error={query.error} retry={() => void reload()} />;
  const account = query.data,
    manage = can("application_account.manage");
  return (
    <Page
      title={account.name}
      description={account.provider_key}
      back={`${basePath}/application-accounts`}
      actions={
        manage && (
          <>
            <Confirm
              title={t(
                account.status === "active"
                  ? "Disable account"
                  : "Enable account",
              )}
              description={t(
                "Administrative availability controls reception and provider dispatch.",
              )}
              trigger={t(account.status === "active" ? "Disable" : "Enable")}
              action={async () => {
                const action =
                    account.status === "active" ? "disable" : "enable",
                  body = { expected_version: account.version };
                await client.http.POST(
                  "/api/v1/application-accounts/{account_id}/{action}",
                  {
                    params: {
                      path: { account_id: account.id, action },
                      header: commandHeaders(
                        workspace.id,
                        key.forBody({ action, ...body }),
                      ),
                    },
                    body,
                  },
                );
                await reload();
              }}
            />
            <Confirm
              title={t("Delete application account")}
              description={t(
                "This makes the identity unavailable and clears its credentials. Retained run evidence keeps its original identity.",
              )}
              trigger={t("Delete")}
              danger
              action={async () => {
                await client.http.DELETE(
                  "/api/v1/application-accounts/{account_id}",
                  {
                    params: {
                      path: { account_id: account.id },
                      query: { expected_version: account.version },
                    },
                  },
                );
                navigate(`${basePath}/application-accounts`);
              }}
            />
          </>
        )
      }
    >
      <Tabs key={generation} defaultValue="details">
        <TabsList aria-label={t("Application account")}>
          <TabsTab value={"credentials"}>{t("Credentials")}</TabsTab>
        </TabsList>
        <TabsPanel value={"credentials"}>
          {<AccountCredentials account={account} reload={reload} />}
        </TabsPanel>
      </Tabs>
    </Page>
  );
}
