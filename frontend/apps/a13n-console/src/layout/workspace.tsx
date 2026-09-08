import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Link, Navigate, Outlet, useLocation, useParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../auth/context";
import { allPages, data, type Schema } from "../shared/api";
import { Empty, ErrorNotice, Loading, Page } from "../shared/feedback";
import { CreateWorkspace } from "../features/settings/create-workspace";

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
  const { workspaceId } = useParams();
  const location = useLocation();
  const organization = auth.data?.organizations[0];
  const userId = auth.data?.user.value.id;
  const workspaces = useQuery({
    queryKey: ["workspaces", organization?.id],
    enabled: !!organization,
    queryFn: async ({ signal }) => ({
      items: await allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization_id}/workspaces", {
            params: {
              path: { organization_id: organization!.id },
              query: { limit: 100, cursor },
            },
            signal,
          })
          .then(data),
      ),
    }),
  });
  const remembered = userId ? lastWorkspaceByUser.get(userId) : undefined;
  const selected =
    workspaceId ??
    workspaces.data?.items.find((item) => item.id === remembered)?.id ??
    workspaces.data?.items[0]?.id;
  const permissions = useQuery({
    queryKey: ["permissions", selected],
    enabled:
      !!selected &&
      !!workspaces.data?.items.some((item) => item.id === selected),
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/permissions", {
          params: { path: { workspace_id: selected! } },
          signal,
        })
        .then(data),
  });
  useEffect(() => {
    if (
      userId &&
      selected &&
      workspaces.data?.items.some((item) => item.id === selected)
    )
      lastWorkspaceByUser.set(userId, selected);
  }, [userId, selected, workspaces.data]);
  if (!auth.isPending && !organization) return <NoOrganization />;
  if (
    auth.isPending ||
    workspaces.isPending ||
    (selected &&
      workspaces.data?.items.some((item) => item.id === selected) &&
      permissions.isPending)
  )
    return <Loading />;
  if (organization && workspaces.data?.items.length === 0)
    return <NoWorkspace organization={organization} />;
  const error = auth.error ?? workspaces.error ?? permissions.error;
  if (error && location.pathname === "/settings/profile") return <Outlet />;
  if (error)
    return (
      <Page title={t("Workspace unavailable")}>
        <ErrorNotice
          error={error}
          retry={() => {
            void workspaces.refetch();
            void permissions.refetch();
          }}
        />
        {workspaces.data?.items
          .filter((item) => item.id !== selected)
          .map((item) => (
            <p key={item.id}>
              <Link to={`/workspaces/${item.id}/agents`}>{item.name}</Link>
            </p>
          ))}
        <Link to="/settings/profile">{t("Personal settings")}</Link>
      </Page>
    );
  const workspace = workspaces.data?.items.find((item) => item.id === selected);
  if (!organization || !workspace || !permissions.data)
    return (
      <Page title={t("Workspace unavailable")}>
        <ErrorNotice error={new Error("Workspace unavailable")} />
        {workspaces.data?.items.map((item) => (
          <p key={item.id}>
            <Link to={`/workspaces/${item.id}/agents`}>{item.name}</Link>
          </p>
        ))}
      </Page>
    );
  if (!workspaceId && window.location.pathname === "/")
    return <Navigate to={`/workspaces/${workspace.id}/agents`} replace />;
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
export function useAccess() {
  const access = useContext(Context);
  if (!access) throw new Error("Missing access provider");
  return access;
}
export function useWorkspace() {
  const access = useAccess();
  if (!access.workspace) throw new Error("Missing workspace provider");
  return { ...access, workspace: access.workspace };
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
        .GET("/api/v1/organizations/{organization_id}/permissions", {
          params: { path: { organization_id: organization.id } },
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
                <Link to="/organization/settings">
                  {t("Organization settings")}
                </Link>
              )}
              <Button
                onClick={() => logout.mutate()}
                loading={logout.isPending}
              >
                {t("Sign out")}
              </Button>
            </>
          }
        >
          <ErrorNotice
            error={permissions.error ?? logout.error}
            retry={() => void permissions.refetch()}
          />
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
          <Button onClick={() => logout.mutate()} loading={logout.isPending}>
            {t("Sign out")}
          </Button>
        }
      >
        <ErrorNotice error={logout.error} />
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
