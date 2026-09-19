import {
  Button,
  ChoiceField,
  FormField,
  Input,
  MenuItem,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { PageActions } from "../../shared/page";

import { KeyIcon, PlusIcon, ProhibitIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
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
import { FormActions, SecretReveal } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import {
  expirationOptions,
  expirationTimestamp,
  type Expiration,
} from "./expiration";

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
    { organization } = useAccess(),
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
          .GET("/api/v1/service-accounts/{account_id}/api-keys", {
            signal,
            params: { path: { account_id: accountId }, query },
          })
          .then(data);
      if (memberKeys)
        return client.http
          .GET("/api/v1/workspaces/{workspace}/api-keys", {
            signal,
            params: { path: { workspace: workspace.id }, query },
          })
          .then(data);
      return client.http
        .GET("/api/v1/workspaces/{workspace}/personal-api-keys", {
          signal,
          params: { path: { workspace: workspace.id }, query },
        })
        .then(data);
    },
  });
  // Member keys name their owner; a raw principal id is the last resort.
  const owners = useQuery({
    queryKey: ["organization-users", organization.id],
    enabled: scope === "member",
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization}/users", {
            params: {
              path: { organization: organization.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const creatable =
    scope === "personal" || (scope === "account" && can("api_key.manage"));
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
                    client.http.POST("/api/v1/api-keys/{key_id}/revoke", {
                      params: { path: { key_id: item.id } },
                    })
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
                      ) => {
                        const owner = owners.data?.find(
                          (user) => user.id === item.principal_id,
                        );
                        return owner ? (
                          <span className={settings.inlineIdentity}>
                            <UserAvatar
                              name={owner.name}
                              id={owner.id}
                              url={owner.image_url}
                              className="size-5 rounded-full"
                            />
                            {owner.name}
                          </span>
                        ) : (
                          item.principal_id
                        );
                      },
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
                    <Timestamp value={item.expires_at} />
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
            count={t("{{count}} keys", { count: items.length })}
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
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [name, setName] = useState(""),
    [expires, setExpires] = useState<Expiration>("never");
  const create = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const body = { name, expires_at: expirationTimestamp(expires) };
      return accountId
        ? client.http
            .POST("/api/v1/service-accounts/{account_id}/api-keys", {
              params: { path: { account_id: accountId } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/personal-api-keys", {
              params: { path: { workspace: workspace.id } },
              body,
            })
            .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["keys", workspace.id] });
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!create.isPending) {
          setOpen(value);
          create.reset();
          setName("");
          setExpires("never");
        }
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
      {create.data ? (
        <div className={styles.stack}>
          <SecretReveal
            value={create.data.bearer}
            label={t("API key")}
            caution={t("Copy this key now. It will not be shown again.")}
          />
          <footer className={styles.formActions}>
            <Button type="button" onClick={() => setOpen(false)}>
              {t("Done")}
            </Button>
          </footer>
        </div>
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <FormField className="min-w-0 w-full" label={t("Name")}>
            <Input
              required={true}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
              autoFocus
            />
          </FormField>
          <ChoiceField
            placeholder={t("Select expiration")}
            value={expires}
            className="min-w-0"
            onValueChange={(value) => {
              const option = expirationOptions.find(
                (option) => option.value === value,
              );
              if (option) setExpires(option.value);
            }}
            label={t("Expires after")}
            options={expirationOptions.map((option) => ({
              ...option,
              label: t(option.label),
            }))}
          />
          <ErrorNotice error={create.error} />
          <FormActions
            onCancel={() => setOpen(false)}
            pending={create.isPending}
            label={t("Create key")}
          />
        </form>
      )}
    </ModalFrame>
  );
}
