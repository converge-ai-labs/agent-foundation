import { Button } from "a13n-ui";

import { useMutation, useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Link, Navigate, Outlet, useLocation, useParams } from "react-router";

import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../auth/context";
import { CreateWorkspace } from "../features/settings/create-workspace";
import { workspacePath } from "../shared/paths";
import { allPages, data, type Schema } from "../shared/api";
import { Empty } from "../shared/collection";
import { ErrorPage, ErrorToast, Loading } from "../shared/feedback";
import { Page } from "../shared/page";

type Verb = Schema["Verb"];
interface WorkspaceContextValue {
  workspace?: Schema["Workspace"];
  organization: Schema["Organization"];
  workspaces: Schema["Workspace"][];
  /** The caller's verb in the current workspace. */
  can: (verb: Verb) => boolean;
  /** The caller's verb at organization scope for administration. */
  organizationCan: (verb: Verb) => boolean;
}
const Context = createContext<WorkspaceContextValue | null>(null);
/** Personal settings render without a workspace, whichever section is open. */
const isPersonalSettings = (pathname: string) =>
  pathname === "/settings" || pathname.startsWith("/settings/");

const lastWorkspaceByUser = new Map<string, string>();
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const auth = useAuth(),
    client = useClient(),
    { t } = useTranslation();
  const route = useParams();
  const location = useLocation();
  const workspaceId = route.workspaceId;
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
  const items = workspaces.data?.items ?? [];
  const remembered = userId ? lastWorkspaceByUser.get(userId) : undefined;
  const workspace = workspaceId
    ? items.find((item) => item.id === workspaceId)
    : // An archived workspace runs nothing, so it is the landing page only when no other exists.
      (items.find((item) => item.id === remembered) ??
      items.find((item) => !item.archived_at) ??
      items[0]);
  const selected = workspace?.id;
  useEffect(() => {
    if (userId && selected) lastWorkspaceByUser.set(userId, selected);
  }, [userId, selected]);
  if (!auth.isPending && !organization) return <NoOrganization />;
  if (auth.isPending || workspaces.isPending) return <Loading page />;
  if (workspaceId && workspaces.isSuccess && !selected)
    return (
      <ErrorPage
        title={t("Not found")}
        error={new Error(t("Resource not found"))}
        actions={<WorkspaceRecoveryActions workspaces={items} />}
      />
    );
  if (organization && workspaces.data?.items.length === 0)
    return <NoWorkspace organization={organization} />;
  const error = auth.error ?? workspaces.error;
  if (error && isPersonalSettings(location.pathname)) return <Outlet />;
  if (error)
    return (
      <ErrorPage
        title={t("Workspace unavailable")}
        error={error}
        actions={
          <WorkspaceRecoveryActions
            workspaces={items}
            currentWorkspaceId={selected}
            retry={() => void workspaces.refetch()}
          />
        }
      />
    );
  if (!organization || !workspace)
    return (
      <ErrorPage
        title={t("Workspace unavailable")}
        error={new Error(t("Workspace unavailable"))}
        actions={<WorkspaceRecoveryActions workspaces={items} />}
      />
    );
  if (!workspaceId && location.pathname === "/")
    return <Navigate to={`${workspacePath(workspace)}/agents`} replace />;
  return (
    <Context.Provider
      value={{
        organization,
        workspace,
        workspaces: workspaces.data!.items,
        can: (verb) => workspace.permissions.includes(verb),
        organizationCan: (verb) => organization.permissions.includes(verb),
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
    auth = useAuth(),
    location = useLocation();
  const admin = organization.permissions.includes("admin");
  const logout = useMutation({ mutationFn: auth.logout });
  return (
    <Context.Provider
      value={{
        organization,
        workspaces: [],
        can: () => false,
        organizationCan: (verb) => organization.permissions.includes(verb),
      }}
    >
      <main id="main-content">
        <Page
          title={organization.name}
          actions={
            <>
              <Link to="/settings/profile">{t("Personal settings")}</Link>
              {admin && (
                <Link to="/organization/settings">
                  {t("Organization settings")}
                </Link>
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
          <ErrorToast error={logout.error} />
          {isPersonalSettings(location.pathname) ||
          location.pathname.startsWith("/organization/settings") ? (
            <Outlet />
          ) : (
            <Empty
              title={t("No workspaces available")}
              description={t(
                admin
                  ? "Create your first workspace to start building agents."
                  : "Ask your organization administrator to grant you workspace access.",
              )}
              action={
                admin && <CreateWorkspace organizationId={organization.id} />
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
        {isPersonalSettings(location.pathname) ? (
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
