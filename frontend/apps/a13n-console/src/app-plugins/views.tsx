import { Button } from "a13n-ui";
import { Component, Suspense, type ComponentType, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useParams } from "react-router";
import { useAccess, useWorkspace } from "../layout/workspace";
import { ErrorPage, Loading, NotFoundPage } from "../shared/feedback";
import { AppContext } from "./context";
import { useAppPlugins } from "./registry";
import { appText, type AppSettings } from "./types";

class ContributionBoundary extends Component<
  {
    children: ReactNode;
    fallback: (error: unknown) => ReactNode;
    resetKey: string;
  },
  { error: unknown }
> {
  state: { error: unknown } = { error: null };
  static getDerivedStateFromError(error: unknown) {
    return { error };
  }
  componentDidUpdate(previous: Readonly<{ resetKey: string }>) {
    if (this.state.error && previous.resetKey !== this.props.resetKey)
      this.setState({ error: null });
  }
  render() {
    return this.state.error
      ? this.props.fallback(this.state.error)
      : this.props.children;
  }
}
export function AppSurface({
  appId,
  component: Content,
}: {
  appId: string;
  component: ComponentType;
}) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { organization, workspace } = useAccess();
  return (
    <ContributionBoundary
      key={`${organization.id}:${workspace?.id}:${appId}`}
      resetKey={pathname}
      fallback={(error) => (
        <ErrorPage
          error={error}
          title={t("App unavailable")}
          actions={
            <Button onClick={() => window.location.reload()}>
              {t("Reload Console")}
            </Button>
          }
        />
      )}
    >
      <AppContext.Provider value={appId}>
        <Suspense fallback={<Loading page />}>
          <Content />
        </Suspense>
      </AppContext.Provider>
    </ContributionBoundary>
  );
}
export function AppRoute() {
  const { appId, pageId } = useParams();
  const { apps, pending, error } = useAppPlugins();
  const { can } = useAccess();
  const { t } = useTranslation();
  const loaded = apps.find((entry) => entry.app.app_id === appId);
  const page = loaded?.contribution?.pages?.find(
    (entry) => entry.id === pageId,
  );
  if (pending) return <Loading page />;
  if (loaded?.error || error)
    return (
      <ErrorPage title={t("App unavailable")} error={loaded?.error ?? error} />
    );
  if (!page || (page.permission && !can(page.permission)))
    return (
      <NotFoundPage
        title={t("App page not found")}
        description={t(
          "Enable the App in organization settings, or choose another page.",
        )}
      />
    );
  return (
    <AppSurface
      key={`${appId}:${pageId}`}
      appId={appId!}
      component={page.component}
    />
  );
}
export function WorkspaceHome() {
  const { apps, pending } = useAppPlugins();
  const { basePath } = useWorkspace();
  const { t } = useTranslation();
  const owners = apps.filter((entry) => entry.contribution?.workspaceHome);
  if (pending) return <Loading page />;
  if (owners.length > 1)
    return (
      <ErrorPage
        title={t("Workspace home unavailable")}
        error={
          new Error(t("More than one enabled App replaces workspace home."))
        }
        actions={
          <Button render={<Link to={`${basePath}/agents`} />}>
            {t("Go to agents")}
          </Button>
        }
      />
    );
  if (owners[0])
    return (
      <AppSurface
        appId={owners[0].app.app_id}
        component={owners[0].contribution!.workspaceHome!}
      />
    );
  return <Navigate to="agents" replace />;
}
export function useAppSettings(scope: AppSettings["scope"]) {
  const { apps } = useAppPlugins();
  const { can, organizationCan } = useAccess();
  const { i18n } = useTranslation();
  return apps.flatMap(({ app, contribution }) =>
    (contribution?.settings ?? [])
      .filter(
        (section) =>
          section.scope === scope &&
          (!section.permission ||
            (scope === "workspace" ? can : organizationCan)(
              section.permission,
            )),
      )
      .map((section) => ({
        appId: app.app_id,
        ...section,
        label: appText(section.label, i18n.language),
        description: section.description
          ? appText(section.description, i18n.language)
          : undefined,
      })),
  );
}
