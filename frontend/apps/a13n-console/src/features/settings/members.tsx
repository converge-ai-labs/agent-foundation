import {
  Button,
  ChoiceField,
  FormField,
  MenuItem,
  ModalFrame,
  SearchPicker,
  Tabs,
  TabsList,
  TabsTab,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement, type ReactNode } from "react";
import { PageActions, useTabParam } from "../../shared/page";

import { ApiError, type Client } from "../../service-client";
import {
  PlusIcon,
  UserMinusIcon,
  UsersIcon,
  UserSwitchIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { useAccess } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { ConflictNotice, Confirm } from "../../shared/dialogs";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { InvitationEditor, Invitations } from "./invitations";
import { roles, type MembershipScope, type Role } from "./roles";

export type { MembershipScope };

/** Members and invitations share one navigation entry and scoped header. */
export function MemberSettings({ scope }: { scope: MembershipScope }) {
  const { t } = useTranslation(),
    { organization, organizationCan } = useAccess();
  const [tab, setTab] = useTabParam(["members", "invitations"]);
  // Both tabs offer the same actions, so the header holds still between them.
  const actions = (
    <>
      {scope.kind === "workspace" && organizationCan("admin") && (
        <AddMember scope={scope} organizationId={organization.id} />
      )}
      <InvitationEditor scope={scope} />
    </>
  );
  return (
    <div className={styles.stack}>
      <PageActions>{actions}</PageActions>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList variant="underline">
          <TabsTab value="members">{t("Members")}</TabsTab>
          <TabsTab value="invitations">{t("Invitations")}</TabsTab>
        </TabsList>
      </Tabs>
      {tab === "invitations" ? (
        <Invitations
          key={`${scope.kind}:${scope.id}`}
          scope={scope}
          actions={actions}
        />
      ) : (
        <Members
          key={`${scope.kind}:${scope.id}`}
          scope={scope}
          actions={actions}
        />
      )}
    </div>
  );
}

/** One page of the grants held at exactly this scope, each naming its principal. */
function grants(
  client: Client,
  scope: MembershipScope,
  query: { cursor?: string; limit: number },
  signal?: AbortSignal,
) {
  return scope.kind === "organization"
    ? client.http
        .GET("/api/v1/organizations/{organization_id}/grants", {
          params: { path: { organization_id: scope.id }, query },
          signal,
        })
        .then(data)
    : client.http
        .GET("/api/v1/workspaces/{workspace_id}/grants", {
          params: { path: { workspace_id: scope.id }, query },
          signal,
        })
        .then(data);
}
function roleOf(grant: Schema["GrantView"]): Role {
  return roles.find((role) => role === grant.role) ?? "viewer";
}

function Members({
  scope,
  actions,
}: {
  scope: MembershipScope;
  actions: ReactNode;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    page = useCursor();
  const bindings = useQuery({
    queryKey: ["bindings", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      grants(client, scope, { cursor: page.cursor, limit: 30 }, signal),
  });
  const items = bindings.data?.items ?? [];
  return (
    <div className={styles.stack}>
      {bindings.isPending ? (
        <Loading variant="table" columns={3} rows={5} />
      ) : bindings.error ? (
        <ErrorNotice error={bindings.error} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Members")}
            rowMenuLabel={t("Member actions")}
            rowMenu={(item) => (
              <>
                <ChangeRole
                  item={item}
                  scope={scope}
                  triggerElement={
                    <MenuItem closeOnClick={false}>
                      <UserSwitchIcon size={14} />
                      {t("Change role")}
                    </MenuItem>
                  }
                />
                <Confirm
                  subject={`${item.principal.name} · ${t(`role.${item.role}`, {
                    defaultValue: item.role,
                  })}`}
                  title={t("Remove member")}
                  description={
                    // A service account is granted only in its home workspace,
                    // so this is its last grant.
                    item.principal.kind === "service_account"
                      ? t(
                          "This removes the service account's only role, which disables it and revokes its keys.",
                        )
                      : t(
                          "This removes the selected role. Other explicit grants may still allow access.",
                        )
                  }
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <UserMinusIcon size={14} />
                      {t("Remove")}
                    </MenuItem>
                  }
                  danger
                  action={() =>
                    scope.kind === "organization"
                      ? client.http.DELETE(
                          "/api/v1/organizations/{organization_id}/grants/{grant_id}",
                          {
                            params: {
                              path: {
                                organization_id: scope.id,
                                grant_id: item.id,
                              },
                            },
                          },
                        )
                      : client.http.DELETE(
                          "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
                          {
                            params: {
                              path: {
                                workspace_id: scope.id,
                                grant_id: item.id,
                              },
                            },
                          },
                        )
                  }
                />
              </>
            )}
            columns={[
              {
                label: t("Member"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={
                      <UserAvatar
                        name={item.principal.name}
                        id={item.principal.id}
                        url={item.principal.image_url}
                        className="size-8 rounded-[8px]"
                      />
                    }
                    name={item.principal.name}
                    description={
                      item.principal.kind === "service_account"
                        ? t("Service account")
                        : (item.principal.email ?? undefined)
                    }
                    resourceId={item.principal.id}
                  />
                ),
              },
              {
                label: t("Role"),
                render: (item) => (
                  <span className={settings.chip}>
                    {t(`role.${item.role}`, {
                      defaultValue: item.role,
                    })}
                  </span>
                ),
              },
              {
                label: t("Joined"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={
              page.previous || bindings.data?.next_cursor
                ? t("{{count}} members on this page", { count: items.length })
                : t("{{count}} members", { count: items.length })
            }
          >
            <Pagination page={page} next={bindings.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<UsersIcon aria-hidden="true" />}
          title={t("No members")}
          description={t("Invite a teammate to start collaborating.")}
          action={actions}
        />
      )}
    </div>
  );
}
/**
 * A role change replaces the grant, so its ID is the version the reader saw:
 * a grant someone replaced or removed meanwhile is `not_found`.
 */
function ChangeRole({
  item,
  scope,
  triggerElement,
}: {
  item: Schema["GrantView"];
  scope: MembershipScope;
  triggerElement?: ReactElement;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [basis, setBasis] = useState(item),
    [role, setRole] = useState<Role>(roleOf(item)),
    [open, setOpen] = useState(false);
  const change = useMutation({
    mutationFn: () =>
      scope.kind === "organization"
        ? client.http.PATCH(
            "/api/v1/organizations/{organization_id}/grants/{grant_id}",
            {
              params: {
                path: { organization_id: scope.id, grant_id: basis.id },
              },
              body: { role },
            },
          )
        : client.http.PATCH(
            "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
            {
              params: { path: { workspace_id: scope.id, grant_id: basis.id } },
              body: { role },
            },
          ),
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  /* The member's current grant at this scope, under whatever ID it now has. */
  const reload = useMutation({
    mutationFn: async () => {
      const latest = (
        await allPages((cursor) =>
          grants(client, scope, { cursor, limit: 100 }),
        )
      ).find((grant) => grant.principal.id === basis.principal.id);
      if (!latest) throw new Error(t("This member no longer has a role here."));
      return latest;
    },
    onSuccess: (latest) => {
      setBasis(latest);
      setRole(roleOf(latest));
      change.reset();
    },
  });
  const conflict =
    change.error instanceof ApiError && change.error.code === "not_found";
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!change.isPending) {
          if (value) {
            setBasis(item);
            setRole(roleOf(item));
          }
          setOpen(value);
          change.reset();
          reload.reset();
        }
      }}
      trigger={
        triggerElement ?? (
          <Button size="sm" variant="outline" type="button">
            {t("Change role")}
          </Button>
        )
      }
      size="md"
      title={t("Change role")}
      description={t("The new role takes effect when you save.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          change.mutate();
        }}
      >
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roles.map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        {conflict && !reload.error ? (
          <ConflictNotice
            title={t("This member changed")}
            description={t(
              "Someone updated this role while the dialog was open. Load the current role before saving.",
            )}
            recover={{
              label: t("Load current role"),
              onClick: () => reload.mutate(),
              pending: reload.isPending,
            }}
          />
        ) : (
          <ErrorNotice
            error={reload.error ?? change.error}
            retry={() => reload.mutate()}
          />
        )}
        <FormActions
          onCancel={() => setOpen(false)}
          pending={change.isPending}
        />
      </form>
    </ModalFrame>
  );
}
function AddMember({
  scope,
  organizationId,
}: {
  scope: MembershipScope;
  organizationId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [userId, setUserId] = useState(""),
    [role, setRole] = useState<Role>("viewer");
  const users = useQuery({
    queryKey: ["organization-users", organizationId],
    enabled: open,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization_id}/members", {
            params: {
              path: { organization_id: organizationId },
              query: { kind: "user", cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const add = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/workspaces/{workspace_id}/grants", {
        params: { path: { workspace_id: scope.id } },
        body: { principal_id: userId, role },
      }),
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="outline" type="button">
          <PlusIcon size={14} />
          {t("Add member")}
        </Button>
      }
      size="md"
      title={t("Add existing member")}
      description={t(
        "Choose someone who already belongs to your organization.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (userId) add.mutate();
        }}
      >
        <FormField label={t("Member")}>
          <SearchPicker
            label={t("Member")}
            placeholder={t("Choose a member…")}
            emptyMessage={t("No members found")}
            value={userId}
            groups={[
              {
                label: t("Organization members"),
                options:
                  users.data?.map((user) => ({
                    value: user.id,
                    label: user.name,
                    description: user.email ?? undefined,
                  })) ?? [],
              },
            ]}
            onValueChange={setUserId}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roles.map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        <ErrorNotice error={users.error ?? add.error} />
        <FormActions
          onCancel={() => setOpen(false)}
          pending={add.isPending}
          label={t("Add member")}
        />
      </form>
    </ModalFrame>
  );
}
