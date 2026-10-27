import { Button, MenuItem, ModalFrame } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { Page, PageActions } from "../../shared/page";

import { KeyIcon, PlusIcon, ProhibitIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag } from "../../shared/api";
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
import { Confirm } from "../../shared/dialogs";
import { SecretReveal } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { CreateKeyForm, useCreateKey } from "./create-key";

export function ApiKeysPage() {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  return (
    <Page
      title={t("API keys")}
      description={t(
        "Your own credentials for this workspace. Keep them private.",
      )}
    >
      <ApiKeys key={workspace.id} />
    </Page>
  );
}

/** Keys a reader can hold: their own, every member's, or one account's. */
type KeyScope = "personal" | "member" | "account";

function keyState(item: {
  revoked_at?: string | null;
  expires_at?: string | null;
}) {
  if (item.revoked_at) return "revoked";
  if (item.expires_at && new Date(item.expires_at).getTime() < Date.now())
    return "expired";
  return "active";
}

/**
 * One table for the three key collections. The columns follow the scope; the
 * page titles and descriptions belong to the settings navigation.
 */
export function ApiKeys({
  accountId,
  memberKeys = false,
  actions = "header",
}: {
  accountId?: string;
  memberKeys?: boolean;
  /** `inline` renders the create trigger where the caller places it. */
  actions?: "header" | "inline";
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    page = useCursor();
  const scope: KeyScope = accountId
    ? "account"
    : memberKeys
      ? "member"
      : "personal";
  const query = useQuery({
    queryKey: ["keys", workspace.id, accountId, memberKeys, page.cursor],
    queryFn: ({ signal }) => {
      const query = { cursor: page.cursor, limit: 30 };
      if (accountId)
        return client.http
          .GET(
            "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}/keys",
            {
              signal,
              params: {
                path: { workspace_id: workspace.id, account_id: accountId },
                query,
              },
            },
          )
          .then(data);
      if (memberKeys)
        return client.http
          .GET("/api/v1/workspaces/{workspace_id}/keys", {
            signal,
            params: { path: { workspace_id: workspace.id }, query },
          })
          .then(data);
      return client.http
        .GET("/api/v1/users/me/keys", {
          signal,
          params: { query: { ...query, workspace_id: workspace.id } },
        })
        .then(data);
    },
  });
  const creatable =
    scope === "personal" || (scope === "account" && can("admin"));
  const create = creatable ? <CreateKey accountId={accountId} /> : null;
  const items = query.data?.items ?? [];
  return (
    <div className={styles.stack}>
      {actions === "header" ? (
        <PageActions>{create}</PageActions>
      ) : (
        create && <div className={settings.inlineAction}>{create}</div>
      )}
      {query.isPending ? (
        <Loading
          variant="table"
          columns={scope === "member" ? 5 : 4}
          rows={5}
        />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("API keys")}
            rowMenuLabel={t("Key actions")}
            rowMenu={(item) =>
              item.revoked_at ? null : (
                <Confirm
                  subject={item.name}
                  title={t("Revoke API key")}
                  description={t(
                    "Applications using this key will lose access immediately.",
                  )}
                  danger
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <ProhibitIcon size={14} />
                      {t("Revoke")}
                    </MenuItem>
                  }
                  action={() =>
                    scope === "personal"
                      ? client.http.DELETE("/api/v1/users/me/keys/{key_id}", {
                          params: { path: { key_id: item.id } },
                          headers: ifMatch(rowTag(item)),
                        })
                      : client.http.DELETE(
                          "/api/v1/workspaces/{workspace_id}/keys/{key_id}",
                          {
                            params: {
                              path: {
                                workspace_id: workspace.id,
                                key_id: item.id,
                              },
                            },
                            headers: ifMatch(rowTag(item)),
                          },
                        )
                  }
                />
              )
            }
            columns={[
              {
                label: t("Name"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={<KeyIcon size={15} aria-hidden="true" />}
                    name={item.name}
                    resourceId={item.id}
                  />
                ),
              },
              ...(scope === "member"
                ? [
                    {
                      label: t("Owner"),
                      render: (
                        item: NonNullable<typeof query.data>["items"][number],
                      ) => (
                        <span className={settings.inlineIdentity}>
                          <UserAvatar
                            name={item.principal.name}
                            id={item.principal.id}
                            url={item.principal.image_url}
                            className="size-5 rounded-md text-[10px]"
                          />
                          <span title={item.principal.name}>
                            {item.principal.name}
                          </span>
                        </span>
                      ),
                    },
                  ]
                : []),
              {
                label: t("Created"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
              {
                label: t("Expires"),
                tone: "muted",
                render: (item) =>
                  item.expires_at ? (
                    <Timestamp value={item.expires_at} relative />
                  ) : (
                    t("No expiration")
                  ),
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={keyState(item)} />,
              },
            ]}
          />
          <CollectionFooter
            count={
              page.previous || query.data.next_cursor
                ? t("{{count}} keys on this page", { count: items.length })
                : t("{{count}} keys", { count: items.length })
            }
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<KeyIcon aria-hidden="true" />}
          title={t("No API keys")}
          description={t(
            scope === "member"
              ? "Member keys appear here once someone creates one."
              : "Create a key when you need to connect an application.",
          )}
          action={create}
        />
      )}
    </div>
  );
}

function CreateKey({
  accountId,
  triggerElement,
}: {
  accountId?: string;
  triggerElement?: ReactElement;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [secret, setSecret] = useState<string>();
  const create = useCreateKey(setSecret, accountId);
  const close = () => {
    setOpen(false);
    setSecret(undefined);
    create.reset();
  };
  return (
    <ModalFrame
      onOpenChange={(value, details) => {
        if (create.isPending) {
          details.cancel();
          return;
        }
        if (value) setOpen(true);
        else close();
      }}
      trigger={
        triggerElement ?? (
          <Button variant="default" type="button">
            <PlusIcon size={14} />
            {t("Create key")}
          </Button>
        )
      }
      size="md"
      title={t("Create API key")}
      description={t(
        "The key belongs to this workspace and is shown only once.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {secret ? (
        <div className={styles.stack}>
          <SecretReveal
            value={secret}
            label={t("API key")}
            caution={t("Copy this key now. It will not be shown again.")}
          />
          <footer className={styles.formActions}>
            <Button type="button" onClick={close}>
              {t("Done")}
            </Button>
          </footer>
        </div>
      ) : (
        open && <CreateKeyForm create={create} onCancel={close} />
      )}
    </ModalFrame>
  );
}
