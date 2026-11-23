import {
  Button,
  ChoiceField,
  FormField,
  Input,
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
import { useAuthConfiguration } from "../../auth/configuration";
import { displayEmail } from "../../shared/identity/display-email";
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
import {
  roles,
  rolesIncluding,
  type MembershipScope,
  type Role,
} from "./roles";

export type { MembershipScope };

/** Members and invitations share one navigation entry and scoped header. */
export function MemberSettings({ scope }: { scope: MembershipScope }) {
  const { t } = useTranslation(),
    { organization, workspace, can, organizationCan } = useAccess();
  const config = useAuthConfiguration();
  const passwords = config.data?.password_enabled === true;
  const organizationAdmin = organizationCan("admin");
  const canManage =
    scope.kind === "organization" ? organizationAdmin : can("admin");
  const canAdd =
    canManage && (scope.kind === "organization" || !workspace?.archived_at);
  const [tab, setTab] = useTabParam(["members", "invitations"]);
  // Both tabs offer the same actions, so the header holds still between them.
  const actions = (
    <>
      {canAdd && (
        <AddMember
          scope={scope}
          organizationId={organizationAdmin ? organization.id : undefined}
        />
      )}
      {canAdd && passwords && <InvitationEditor scope={scope} />}
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
          canResend={canAdd && passwords}
        />
      ) : (
        <Members
          key={`${scope.kind}:${scope.id}`}
          scope={scope}
          actions={actions}
          canManage={canManage}
          canChange={canAdd}
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
function hasSSODefault(item: Schema["GrantView"]) {
  return item.source === "oidc" || item.overrides_sso === true;
}

function Members({
  scope,
  actions,
  canManage,
  canChange,
}: {
  scope: MembershipScope;
  actions: ReactNode;
  canManage: boolean;
  canChange: boolean;
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
            rowMenu={
              canManage
                ? (item) => (
                    <>
                      {canChange && (
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
                      )}
                      {!hasSSODefault(item) && (
                        <Confirm
                          subject={`${item.principal.name} · ${t(
                            `role.${item.role}`,
                            {
                              defaultValue: item.role,
                            },
                          )}`}
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
                            <MenuItem
                              closeOnClick={false}
                              variant="destructive"
                            >
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
                      )}
                    </>
                  )
                : undefined
            }
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
                        : displayEmail(item.principal)
                    }
                    resourceId={item.principal.id}
                  />
                ),
              },
              {
                label: t("Role"),
                render: (item) => (
                  <span className="inline-flex items-center gap-2">
                    <span className={settings.chip}>
                      {t(`role.${item.role}`, { defaultValue: item.role })}
                    </span>
                    {hasSSODefault(item) && (
                      <span className="text-xs text-muted-foreground">
                        {item.source === "oidc" ? t("SSO") : t("Manual")}
                      </span>
                    )}
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
          description={t("No roles have been assigned here yet.")}
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
    [role, setRole] = useState(item.role),
    [mode, setMode] = useState(item.source ?? "manual"),
    [open, setOpen] = useState(false);
  const choosingSSO = hasSSODefault(basis);
  const restoreDefault = choosingSSO && mode === "oidc";
  const noChange = restoreDefault && basis.source === "oidc";
  const change = useMutation({
    mutationFn: async () => {
      if (scope.kind === "organization") {
        const path = { organization_id: scope.id, grant_id: basis.id };
        if (restoreDefault) {
          await client.http.DELETE(
            "/api/v1/organizations/{organization_id}/grants/{grant_id}",
            { params: { path } },
          );
        } else if (basis.source === "oidc") {
          await client.http.POST(
            "/api/v1/organizations/{organization_id}/grants",
            {
              params: { path: { organization_id: scope.id } },
              body: { principal_id: basis.principal.id, role },
            },
          );
        } else {
          await client.http.PATCH(
            "/api/v1/organizations/{organization_id}/grants/{grant_id}",
            {
              params: { path },
              body: { role },
            },
          );
        }
      } else {
        const path = { workspace_id: scope.id, grant_id: basis.id };
        if (restoreDefault) {
          await client.http.DELETE(
            "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
            { params: { path } },
          );
        } else if (basis.source === "oidc") {
          await client.http.POST("/api/v1/workspaces/{workspace_id}/grants", {
            params: { path: { workspace_id: scope.id } },
            body: { principal_id: basis.principal.id, role },
          });
        } else {
          await client.http.PATCH(
            "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
            {
              params: { path },
              body: { role },
            },
          );
        }
      }
    },
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
      setRole(latest.role);
      setMode(latest.source ?? "manual");
      change.reset();
    },
  });
  const conflict =
    change.error instanceof ApiError && change.error.code === "not_found";
  return (
    <ModalFrame
      onOpenChange={(value) => {
        setOpen(value);
        if (!change.isPending) {
          if (value) {
            setBasis(item);
            setRole(item.role);
            setMode(item.source ?? "manual");
          }
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
          if (!noChange) change.mutate();
        }}
      >
        {choosingSSO && (
          <ChoiceField
            label={t("Role source")}
            value={mode}
            onValueChange={(value) => {
              if (value === "oidc" || value === "manual") setMode(value);
            }}
            options={[
              { value: "oidc", label: t("Use SSO default") },
              { value: "manual", label: t("Set role manually") },
            ]}
          />
        )}
        {!restoreDefault && (
          <ChoiceField
            placeholder={t("Select role")}
            value={role}
            className="min-w-0"
            onValueChange={(value) => {
              if (value) setRole(value);
            }}
            label={t("Role")}
            options={rolesIncluding(basis.role).map((value) => ({
              value,
              label: t(`role.${value}`, { defaultValue: value }),
            }))}
          />
        )}
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
          disabled={noChange}
          label={
            restoreDefault && !noChange ? t("Restore SSO default") : undefined
          }
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
  organizationId?: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [userId, setUserId] = useState(""),
    [role, setRole] = useState<Role>("viewer");
  const users = useQuery({
    queryKey: ["organization-users", organizationId],
    enabled: open && !!organizationId,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization_id}/members", {
            params: {
              path: { organization_id: organizationId! },
              query: { kind: "user", cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const existing = useQuery({
    queryKey: ["bindings", scope.kind, scope.id, "existing"],
    enabled: open && !!organizationId,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        grants(client, scope, { cursor, limit: 100 }, signal),
      ),
  });
  const candidates =
    users.data &&
    existing.data &&
    (() => {
      const granted = new Set(existing.data.map((item) => item.principal.id));
      return users.data.filter(
        (user) => user.status === "active" && !granted.has(user.id),
      );
    })();
  const principalId = userId.trim();
  const canSubmit =
    !!principalId &&
    (!organizationId || !!candidates?.some((user) => user.id === principalId));
  const add = useMutation({
    mutationFn: () => {
      const body = { principal_id: principalId, role };
      return scope.kind === "organization"
        ? client.http.POST("/api/v1/organizations/{organization_id}/grants", {
            params: { path: { organization_id: scope.id } },
            body,
          })
        : client.http.POST("/api/v1/workspaces/{workspace_id}/grants", {
            params: { path: { workspace_id: scope.id } },
            body,
          });
    },
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (value && !add.isPending) {
          setUserId("");
          setRole("viewer");
          add.reset();
        }
        setOpen(value);
      }}
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
          if (canSubmit && !add.isPending) add.mutate();
        }}
      >
        {organizationId ? (
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
                    candidates?.map((user) => ({
                      value: user.id,
                      label: user.name,
                      description: displayEmail(user) ?? user.id,
                    })) ?? [],
                },
              ]}
              onValueChange={setUserId}
            />
          </FormField>
        ) : (
          <FormField label={t("Principal ID")}>
            <Input
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              required
            />
          </FormField>
        )}
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
        <ErrorNotice error={users.error ?? existing.error ?? add.error} />
        <FormActions
          onCancel={() => setOpen(false)}
          pending={add.isPending}
          disabled={!canSubmit}
          label={t("Add member")}
        />
      </form>
    </ModalFrame>
  );
}
