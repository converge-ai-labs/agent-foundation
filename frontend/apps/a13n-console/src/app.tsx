import { lazy, Suspense, useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router";
import { useTranslation } from "react-i18next";
import { AuthProvider, useAuth } from "./auth/context";
import { AuthPage } from "./auth/pages";
import { WorkspaceProvider } from "./layout/workspace";
import { Shell } from "./layout/shell";
import { ErrorNotice, Loading, Empty, Page } from "./shared/feedback";
import "./app.css";
import { AppearanceProvider } from "./layout/appearance";

const ModelsPage = lazy(() =>
  import("./features/models/page").then((module) => ({
    default: module.ModelsPage,
  })),
);
const Agents = lazy(() =>
  import("./features/agents/list").then((module) => ({
    default: module.Agents,
  })),
);
const AgentDetail = lazy(() =>
  import("./features/agents/detail").then((module) => ({
    default: module.AgentDetail,
  })),
);
const CreateAgent = lazy(() =>
  import("./features/agents/detail").then((module) => ({
    default: module.CreateAgent,
  })),
);
const PersonalSettings = lazy(() =>
  import("./features/settings/personal").then((module) => ({
    default: module.PersonalSettings,
  })),
);
const OrganizationSettings = lazy(() =>
  import("./features/settings/pages").then((module) => ({
    default: module.OrganizationSettings,
  })),
);
const WorkspaceSettings = lazy(() =>
  import("./features/settings/pages").then((module) => ({
    default: module.WorkspaceSettings,
  })),
);
const AssetsPage = lazy(() =>
  import("./features/assets/page").then((module) => ({
    default: module.AssetsPage,
  })),
);
const SkillsPage = lazy(() =>
  import("./features/skills/page").then((module) => ({
    default: module.SkillsPage,
  })),
);
const SkillDetail = lazy(() =>
  import("./features/skills/page").then((module) => ({
    default: module.SkillDetail,
  })),
);
const EnvironmentsPage = lazy(() =>
  import("./features/environments/page").then((module) => ({
    default: module.EnvironmentsPage,
  })),
);
const ConnectorsPage = lazy(() =>
  import("./features/connectors/page").then((module) => ({
    default: module.ConnectorsPage,
  })),
);
const MCPPage = lazy(() =>
  import("./features/mcp/page").then((module) => ({ default: module.MCPPage })),
);
const ApplicationAccountsPage = lazy(() =>
  import("./features/application-accounts/page").then((module) => ({
    default: module.ApplicationAccountsPage,
  })),
);
const ApplicationAccountDetail = lazy(() =>
  import("./features/application-accounts/page").then((module) => ({
    default: module.ApplicationAccountDetail,
  })),
);
const TracesPage = lazy(() =>
  import("./features/traces/page").then((module) => ({
    default: module.TracesPage,
  })),
);
const TraceDetailPage = lazy(() =>
  import("./features/traces/detail").then((module) => ({
    default: module.TraceDetailPage,
  })),
);
const ConversationsPage = lazy(() =>
  import("./features/conversations/page").then((module) => ({
    default: module.ConversationsPage,
  })),
);
const NewConversation = lazy(() =>
  import("./features/conversations/page").then((module) => ({
    default: module.NewConversation,
  })),
);
const SessionLayout = lazy(() =>
  import("./features/conversations/page").then((module) => ({
    default: module.SessionLayout,
  })),
);
const ThreadLayout = lazy(() =>
  import("./features/conversations/page").then((module) => ({
    default: module.ThreadLayout,
  })),
);
const RunPage = lazy(() =>
  import("./features/conversations/run").then((module) => ({
    default: module.RunPage,
  })),
);

function Authenticated() {
  const auth = useAuth();
  if (auth.isPending) return <Loading />;
  if (auth.anonymous) return <Navigate to="/login" replace />;
  if (auth.error)
    return <ErrorNotice error={auth.error} retry={() => void auth.refetch()} />;
  return <Outlet />;
}
function WorkspaceShell() {
  return (
    <WorkspaceProvider>
      <Shell />
    </WorkspaceProvider>
  );
}
function ComingSoon() {
  const { t } = useTranslation(),
    location = useLocation();
  const title = location.pathname.endsWith("usage")
    ? t("Usage")
    : t("Schedules");
  return (
    <Page title={title}>
      <Empty
        title={t("Coming soon")}
        description={t("This capability is planned for a future release.")}
      />
    </Page>
  );
}
function NotFound() {
  const { t } = useTranslation();
  return (
    <Page title={t("Page not found")}>
      <Empty
        title={t("Nothing here")}
        description={t("Choose a page from the navigation to continue.")}
      />
    </Page>
  );
}
export function App() {
  return (
    <AppearanceProvider>
      <AppContent />
    </AppearanceProvider>
  );
}
function AppContent() {
  const { i18n } = useTranslation();
  useEffect(() => {
    document.documentElement.lang = i18n.resolvedLanguage ?? "en";
  }, [i18n.resolvedLanguage]);
  return (
    <div className="a13n-root">
      <a href="#main-content" className="skip-link">
        {i18n.t("Skip to content")}
      </a>
      <BrowserRouter>
        <AuthProvider>
          <Suspense fallback={<Loading />}>
            <Routes>
              {[
                "/login",
                "/forgot-password",
                "/reset-password",
                "/confirm-email",
                "/invitations/:invitationId/accept",
              ].map((path) => (
                <Route
                  key={path}
                  path={path}
                  element={<AuthPage key={path} />}
                />
              ))}
              <Route element={<Authenticated />}>
                <Route
                  path="/workspaces/:workspaceId"
                  element={<WorkspaceShell />}
                >
                  <Route index element={<Navigate to="agents" replace />} />
                  <Route path="agents" element={<Agents />} />
                  <Route path="agents/new" element={<CreateAgent />} />
                  <Route path="agents/:agentId" element={<AgentDetail />} />
                  <Route path="sessions" element={<ConversationsPage />}>
                    <Route path="new" element={<NewConversation />} />
                    <Route path=":sessionId" element={<SessionLayout />}>
                      <Route
                        path="threads/:threadId"
                        element={<ThreadLayout />}
                      >
                        <Route path="runs/:runId" element={<RunPage />} />
                      </Route>
                    </Route>
                  </Route>
                  <Route path="traces" element={<TracesPage />} />
                  <Route path="traces/:traceId" element={<TraceDetailPage />} />
                  <Route
                    path="application-accounts"
                    element={<ApplicationAccountsPage />}
                  />
                  <Route
                    path="application-accounts/:accountId"
                    element={<ApplicationAccountDetail />}
                  />
                  <Route path="connectors" element={<ConnectorsPage />} />
                  <Route
                    path="connectors/providers"
                    element={<ConnectorsPage providers />}
                  />
                  <Route path="mcp" element={<MCPPage />} />
                  <Route path="environments" element={<EnvironmentsPage />} />
                  <Route
                    path="environments/providers"
                    element={<EnvironmentsPage section="providers" />}
                  />
                  <Route
                    path="environments/instances"
                    element={<EnvironmentsPage section="instances" />}
                  />
                  <Route path="assets" element={<AssetsPage />} />
                  <Route path="skills" element={<SkillsPage />} />
                  <Route path="skills/:skillId" element={<SkillDetail />} />
                  <Route path="models" element={<ModelsPage />} />
                  <Route
                    path="models/providers"
                    element={<ModelsPage providers />}
                  />
                  <Route path="settings" element={<WorkspaceSettings />} />
                  <Route path="usage" element={<ComingSoon />} />
                  <Route path="schedules" element={<ComingSoon />} />
                  <Route path="*" element={<NotFound />} />
                </Route>
                <Route element={<WorkspaceShell />}>
                  <Route path="/" element={null} />
                  <Route
                    path="/settings/profile"
                    element={<PersonalSettings />}
                  />
                  <Route
                    path="/organization/settings"
                    element={<OrganizationSettings />}
                  />
                </Route>
              </Route>
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}
