import { Button } from "a13n-ui";

import { useMutation, useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Link, Navigate, Outlet, useLocation, useParams } from "react-router";

import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../auth/context";
import { CreateWorkspace } from "../features/settings/create-workspace";
import { workspacePath } from "../shared/paths";
import { allPages, data, type Schema } from "../shared/api";
import {
  Empty,
  ErrorNotice,
  ErrorPage,
  ErrorToast,
  Loading,
  Page,
} from "../shared/feedback";

interface WorkspaceContextValue {
  workspace?: Schema["Workspace"];
  organization: Schema["Organization"];
  workspaces: Schema["Workspace"][];
  can: (action: string) => boolean;
  organizationAdmin: boolean;
}
const Context = createContext<WorkspaceContextValue | null>(null);
const lastWorkspaceByUser = new Map<string, string>();
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const auth = useAuth(),
    client = useClient(),
    { t } = useTranslation();
  const route = useParams();
  const location = useLocation();
  const workspaceKey = route.workspaceKey;
  const organization = auth.data?.organizations[0];
  const userId = auth.data?.user.value.id;
  const workspaces = useQuery({
    queryKey: ["workspaces", organization?.id],
    enabled: !!organization,
    queryFn: async ({ signal }) => ({
      items: await allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization}/workspaces", {
            params: {
              path: { organization: organization!.id },
              query: { limit: 100, cursor },
            },
            signal,
          })
          .then(data),
      ),
    }),
  });
  const items = workspaces.data?.items ?? [];
  const remembered = userId ? lastWorkspaceByUser.get(userId) : undefined;
  const workspace = workspaceKey
    ? items.find((item) => item.key === workspaceKey)
    : (items.find((item) => item.id === remembered) ?? items[0]);
  const selected = workspace?.id;
  const permissions = useQuery({
    queryKey: ["permissions", selected],
    enabled: !!selected,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/permissions", {
          params: { path: { workspace: selected! } },
          signal,
        })
        .then(data),
  });
  useEffect(() => {
    if (userId && selected) lastWorkspaceByUser.set(userId, selected);
  }, [userId, selected]);
  if (!auth.isPending && !organization) return <NoOrganization />;
  if (
    auth.isPending ||
    workspaces.isPending ||
    (selected && permissions.isPending)
  )
    return <Loading page />;
  if (workspaceKey && workspaces.isSuccess && !selected)
    return (
      <ErrorPage
        title={t("Not found")}
        error={new Error(t("Resource not found"))}
        actions={<WorkspaceRecoveryActions workspaces={items} />}
      />
    );
  if (organization && workspaces.data?.items.length === 0)
    return <NoWorkspace organization={organization} />;
  const error = auth.error ?? workspaces.error ?? permissions.error;
  if (error && location.pathname === "/settings/profile") return <Outlet />;
  if (error)
    return (
      <ErrorPage
        title={t("Workspace unavailable")}
        error={error}
        actions={
          <WorkspaceRecoveryActions
            workspaces={items}
            currentWorkspaceId={selected}
            retry={() => {
              void workspaces.refetch();
              if (selected) void permissions.refetch();
            }}
          />
        }
      />
    );
  if (!organization || !workspace || !permissions.data)
    return (
      <ErrorPage
        title={t("Workspace unavailable")}
        error={new Error(t("Workspace unavailable"))}
        actions={<WorkspaceRecoveryActions workspaces={items} />}
      />
    );
  if (!workspaceKey && location.pathname === "/")
    return <Navigate to={`${workspacePath(workspace)}/agents`} replace />;
  return (
    <Context.Provider
      value={{
        organization,
        workspace,
        workspaces: workspaces.data!.items,
        can: (action) => permissions.data!.actions.includes(action),
        organizationAdmin: permissions.data.organization_admin,
      }}
    >
      {children}
    </Context.Provider>
  );
}

function WorkspaceRecoveryActions({
  workspaces,
  currentWorkspaceId,
  retry,
}: {
  workspaces: Schema["Workspace"][];
  currentWorkspaceId?: string;
  retry?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      {retry && <Button onClick={retry}>{t("Try again")}</Button>}
      {workspaces
        .filter((workspace) => workspace.id !== currentWorkspaceId)
        .map((workspace) => (
          <Button
            key={workspace.id}
            variant="outline"
            render={<Link to={`${workspacePath(workspace)}/agents`} />}
          >
            {t("Switch workspace")}: {workspace.name}
          </Button>
        ))}
      <Button variant="ghost" render={<Link to="/settings/profile" />}>
        {t("Personal settings")}
      </Button>
    </>
  );
}
export function useAccess() {
  const access = useContext(Context);
  if (!access) throw new Error("Missing access provider");
  return access;
}
export function useWorkspace() {
  const access = useAccess();
  if (!access.workspace) throw new Error("Missing workspace provider");
  return {
    ...access,
    workspace: access.workspace,
    basePath: workspacePath(access.workspace),
  };
}

function NoWorkspace({
  organization,
}: {
  organization: Schema["Organization"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    auth = useAuth(),
    location = useLocation();
  const permissions = useQuery({
    queryKey: ["organization-permissions", organization.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/organizations/{organization}/permissions", {
          params: { path: { organization: organization.id } },
          signal,
        })
        .then(data),
  });
  const logout = useMutation({ mutationFn: auth.logout });
  return (
    <Context.Provider
      value={{
        organization,
        workspaces: [],
        organizationAdmin: permissions.data?.organization_admin ?? false,
        can: () => false,
      }}
    >
      <main id="main-content">
        <Page
          title={organization.name}
          actions={
            <>
              <Link to="/settings/profile">{t("Personal settings")}</Link>
              {permissions.data?.organization_admin && (
                <>
                  <Link to="/organization/settings">
                    {t("Organization settings")}
                  </Link>
                  <Link to="/organization/settings?section=providers">
                    {t("Providers")}
                  </Link>
                </>
              )}
              <Button
                variant="outline"
                loading={logout.isPending}
                onClick={() => logout.mutate()}
                type="button"
              >
                {t("Sign out")}
              </Button>
            </>
          }
        >
          <ErrorNotice
            error={permissions.error}
            retry={() => void permissions.refetch()}
          />
          <ErrorToast error={logout.error} />
          {["/settings/profile", "/organization/settings"].includes(
            location.pathname,
          ) && !permissions.isPending ? (
            <Outlet />
          ) : permissions.isPending ? (
            <Loading />
          ) : (
            <Empty
              title={t("No workspaces available")}
              description={t(
                permissions.data?.organization_admin
                  ? "Create your first workspace to start building agents."
                  : "Ask your organization administrator to grant you workspace access.",
              )}
              action={
                permissions.data?.organization_admin && (
                  <CreateWorkspace organizationId={organization.id} />
                )
              }
            />
          )}
        </Page>
      </main>
    </Context.Provider>
  );
}

function NoOrganization() {
  const auth = useAuth(),
    { t } = useTranslation(),
    location = useLocation();
  const logout = useMutation({ mutationFn: auth.logout });
  return (
    <main id="main-content">
      <Page
        title={t("No organization access")}
        actions={
          <Button
            variant="outline"
            loading={logout.isPending}
            onClick={() => logout.mutate()}
            type="button"
          >
            {t("Sign out")}
          </Button>
        }
      >
        <ErrorToast error={logout.error} />
        {location.pathname === "/settings/profile" ? (
          <Outlet />
        ) : (
          <>
            <p>
              {t(
                "Ask an organization administrator to invite you or restore your membership.",
              )}
            </p>
            <Link to="/settings/profile">{t("Personal settings")}</Link>
          </>
        )}
      </Page>
    </main>
  );
}
