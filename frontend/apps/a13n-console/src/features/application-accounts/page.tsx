import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button, Dialog, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data } from "../../shared/api";
import {
  Page,
  ErrorNotice,
  Empty,
  Loading,
  StateBadge,
} from "../../shared/feedback";
import { Confirm, JsonView } from "../../shared/form";
import { Table, Pagination, useCursor } from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { AccountForm } from "./form";
import { AccountCredentials } from "./credentials";
import { AccountTargets } from "./targets";

export function ApplicationAccountsPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [open, setOpen] = useState(false),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["application-accounts", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/application-accounts", {
          params: {
            path: { workspace_id: workspace.id },
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
          <Dialog
            size="wide"
            title={t("Add application account")}
            description={t(
              "Configure one concrete external identity and its reception settings.",
            )}
            closeLabel={t("Close")}
            open={open}
            onOpenChange={setOpen}
            trigger={<Button variant="primary">{t("Add account")}</Button>}
          >
            {open && (
              <AccountForm
                onSuccess={(account) => {
                  setOpen(false);
                  navigate(account.id);
                }}
              />
            )}
          </Dialog>
        )
      }
    >
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
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
    { workspace, can } = useWorkspace(),
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
      back={`/workspaces/${workspace.id}/application-accounts`}
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
                navigate(`/workspaces/${workspace.id}/application-accounts`);
              }}
            />
          </>
        )
      }
    >
      <Tabs
        key={generation}
        label={t("Application account")}
        defaultValue="details"
        items={[
          {
            value: "details",
            label: t("Details"),
            content: manage ? (
              <AccountForm
                initial={account}
                onSuccess={() => void reload()}
                reload={reload}
              />
            ) : (
              <JsonView value={account} />
            ),
          },
          {
            value: "targets",
            label: t("Targets"),
            content: <AccountTargets account={account} />,
          },
          ...(manage
            ? [
                {
                  value: "credentials",
                  label: t("Credentials"),
                  content: (
                    <AccountCredentials account={account} reload={reload} />
                  ),
                },
              ]
            : []),
        ]}
      />
    </Page>
  );
}
