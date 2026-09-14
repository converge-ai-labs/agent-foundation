import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import {
  Button,
  Wordmark,
  ChoiceField,
  ModalFrame,
  Sheet,
  SheetPopup,
  SheetTitle,
  SheetTrigger,
} from "a13n-ui";
import {
  House,
  Folder,
  SlidersHorizontal,
  PlugsConnected,
  Moon,
  Sun,
  List,
  Users,
  ArrowRight,
  Gear,
} from "@phosphor-icons/react";
import {
  useProjects,
  useSetup,
  useSources,
  useStatus,
  useTransport,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { AccountsPage } from "../setup/accounts";
import { SetupPage } from "../setup/setup";
import { SourcesPage, SourcePage } from "../configuration/sources";
import {
  SettingsLayout,
  GeneralSettings,
  CapabilitiesPage,
  EnvironmentsPage,
} from "../configuration/settings";
import { ProjectsPage, ProjectPage } from "../configuration/projects";
import { ErrorNotice, PageHeader, Panel, TextField } from "./ui";
import { useLiveWorkbench, type Profile } from "./presence";
import styles from "./workbench.module.css";
import { ConversationNavigation } from "../conversations/navigation";
import { ConversationPage } from "../conversations/conversation";
import { NativeWorkspace } from "../native/workspace";

import { pageLink } from "./page-links";
import { readPreference, writePreference } from "./preferences";

export function Workbench({
  status: initialStatus,
  forget,
  unauthorized,
}: {
  status: Schema<"ListenerStatus">;
  forget: () => void;
  unauthorized: () => void;
}) {
  const statusQuery = useStatus();
  const status = statusQuery.data ?? initialStatus;
  const [theme, setTheme] = useState(() => readPreference("theme", "light"));
  const [profile, setProfile] = useState<Profile>(() => ({
    display_name: readPreference("display-name", ""),
    color: readPreference("color", "#64748b"),
  }));
  const [nativeFocus, setNativeFocus] = useState<Schema<"PageTarget"> | null>(
    null,
  );
  const sources = useSources();
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const live = useLiveWorkbench(
    profile,
    !!status.features?.page_presence,
    unauthorized,
    nativeFocus,
  );
  const location = useLocation();
  const navigate = useNavigate();
  const { client } = useTransport();
  const [restoreTarget, setRestoreTarget] = useState(() =>
    location.pathname === "/" && !location.search
      ? readPreference("last-thread", "")
      : "",
  );
  useEffect(() => {
    if (!restoreTarget) return;
    if (location.pathname !== "/") {
      setRestoreTarget("");
      return;
    }
    const abort = new AbortController();
    void result(
      client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: restoreTarget } },
        signal: abort.signal,
      }),
    ).then(
      (detail) => {
        if (abort.signal.aborted) return;
        setRestoreTarget("");
        if (!detail.thread.archived && !detail.thread.parent_thread_id)
          navigate(`/threads/${encodeURIComponent(restoreTarget)}`, {
            replace: true,
          });
      },
      () => {
        if (!abort.signal.aborted) setRestoreTarget("");
      },
    );
    return () => abort.abort();
  }, [client, restoreTarget, location.pathname, navigate]);
  useEffect(() => {
    setMenu(false);
  }, [location.pathname]);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    writePreference("theme", theme);
  }, [theme]);
  const updateProfile = (next: Profile) => {
    setProfile(next);
    writePreference("display-name", next.display_name);
    writePreference("color", next.color);
  };
  const links = [
    { to: "/", label: "Overview", icon: House },
    { to: "/settings", label: "Settings", icon: Gear },
  ];
  const navigation = (
    <>
      <ConversationNavigation />
      <nav>
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              isActive ? styles.activeNav : styles.navLink
            }
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
  return (
    <Sheet open={menu} onOpenChange={setMenu}>
      <div className={styles.shell}>
        <a className={styles.skipLink} href="#main-content">
          Skip to content
        </a>
        <aside className={styles.sidebar} aria-label="Workbench navigation">
          <Link to="/" className={styles.brand}>
            <Wordmark className={styles.brandMark} />
            <span>Harness UI</span>
          </Link>
          {navigation}
          <div className={styles.sidebarFooter}>
            <span className={styles.statusDot} />
            {status.app?.state ?? "Connected"}
            <small>
              Version <span>{status.version}</span>
            </small>
          </div>
        </aside>
        <SheetPopup
          side="left"
          className={styles.mobileNavigation}
          closeProps={{ "aria-label": "Close navigation" }}
        >
          <SheetTitle>Harness UI</SheetTitle>
          {navigation}
        </SheetPopup>
        <div className={styles.workspace}>
          <header className={styles.topbar}>
            <div className={styles.actions}>
              <SheetTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon"
                    className={styles.mobileMenu}
                  />
                }
                aria-label="Open navigation"
              >
                <List />
              </SheetTrigger>
              <span className={styles.instanceLabel}>Local instance</span>
              <span className={styles.connectionState}>{live.summary}</span>
            </div>
            <div className={styles.actions}>
              <Button variant="ghost" onClick={() => setPeopleOpen(true)}>
                <Users />
                <span>{live.presence?.participants.length ?? 0} online</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={
                  theme === "dark"
                    ? "Switch to light theme"
                    : "Switch to dark theme"
                }
                onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              >
                {theme === "dark" ? <Sun /> : <Moon />}
              </Button>
              <Button variant="ghost" onClick={forget}>
                Log out
              </Button>
            </div>
          </header>
          <main id="main-content" className={styles.main}>
            {status.access === "dangerous_bypass" && (
              <div className={styles.notice}>
                Instance authentication is disabled by the server's explicit
                dangerous bypass setting.
              </div>
            )}
            {status.app?.candidate_error_message && (
              <div role="alert" className={styles.notice}>
                <strong>Configuration could not be updated</strong>
                <p>{status.app.candidate_error_message}</p>
                <p>
                  Your previous settings are still active. Open Settings to
                  inspect the saved configuration and correct the changes.
                </p>
              </div>
            )}
            <ErrorNotice
              error={statusQuery.error}
              retry={() => void statusQuery.refetch()}
            />
            <NativeWorkspace
              onFocus={setNativeFocus}
              unauthorized={unauthorized}
            >
              <Routes>
                <Route path="/" element={<HomePage />} />
                <Route
                  path="/threads/:threadId"
                  element={
                    <ConversationPage
                      profile={profile}
                      unauthorized={unauthorized}
                    />
                  }
                />
                <Route element={<SettingsLayout />}>
                  <Route path="/setup" element={<SetupPage />} />
                  <Route path="/projects" element={<ProjectsPage />} />
                  <Route
                    path="/projects/:projectId"
                    element={<ProjectPage />}
                  />
                  <Route path="/settings" element={<GeneralSettings />} />
                  <Route
                    path="/settings/agents"
                    element={
                      <SourcesPage
                        kinds={["agent", "model"]}
                        title="Agents & models"
                        description="Configure how your agents work and which models they use."
                      />
                    }
                  />
                  <Route
                    path="/settings/capabilities"
                    element={<CapabilitiesPage />}
                  />
                  <Route
                    path="/settings/environments"
                    element={<EnvironmentsPage />}
                  />
                  <Route
                    path="/settings/connections"
                    element={
                      <SourcesPage
                        kinds={["mcp_server"]}
                        title="MCP connections"
                        description="Connect tools and data sources, then choose which agents can use them."
                      />
                    }
                  />
                  <Route path="/settings/resources" element={<SourcesPage />} />
                  <Route path="/settings/source" element={<SourcePage />} />
                  <Route path="/settings/accounts" element={<AccountsPage />} />
                  <Route
                    path="/settings/catalog"
                    element={<Navigate to="/settings/capabilities" replace />}
                  />
                </Route>
                <Route
                  path="*"
                  element={
                    <Panel title="Page unavailable">
                      <p>This page is not available in this build.</p>
                      <Link to="/">Return to the workbench</Link>
                    </Panel>
                  }
                />
              </Routes>
            </NativeWorkspace>
          </main>
        </div>
        <ModalFrame
          open={peopleOpen}
          onOpenChange={setPeopleOpen}
          title="People in this workspace"
          description="See who is connected and what they are viewing. Your display name helps others recognize this tab."
          closeLabel="Close"
        >
          <div className={styles.stack}>
            <TextField
              label="Your display name"
              value={profile.display_name}
              onChange={(display_name) =>
                updateProfile({
                  ...profile,
                  display_name: display_name.slice(0, 80),
                })
              }
            />
            <ChoiceField
              label="Your color"
              value={profile.color}
              onValueChange={(color) => updateProfile({ ...profile, color })}
              options={[
                { value: "#64748b", label: "Slate" },
                { value: "#2563eb", label: "Blue" },
                { value: "#7c3aed", label: "Purple" },
                { value: "#059669", label: "Green" },
                { value: "#d97706", label: "Amber" },
              ]}
            />
            <p>Presence: {live.presenceState}</p>
            {live.presence?.participants.map((participant) => (
              <div
                className={styles.resourceRow}
                key={participant.participant_id}
              >
                <strong>
                  {participant.display_name || "Anonymous"}
                  {participant.participant_id === live.presence?.participant_id
                    ? " (you)"
                    : ""}
                </strong>
                <span>{participant.foreground ? "Active tab" : "Away"}</span>
                <small>
                  {live.presence?.same_page_participant_ids?.includes(
                    participant.participant_id,
                  )
                    ? "Same page"
                    : participant.availability}
                </small>
                {participant.focus && (
                  <small>{participant.focus.target.kind}</small>
                )}
                {participant.focus &&
                  participant.availability === "available" &&
                  pageLink(participant.focus, sources.data?.sources) && (
                    <Link
                      to={pageLink(participant.focus, sources.data?.sources)!}
                      onClick={() => setPeopleOpen(false)}
                    >
                      Open page
                    </Link>
                  )}
                {participant.unavailable_reason && (
                  <small>{participant.unavailable_reason}</small>
                )}
              </div>
            ))}
          </div>
        </ModalFrame>
      </div>
    </Sheet>
  );
}
function HomePage() {
  const setup = useSetup();
  const projects = useProjects();
  const sources = useSources();
  const status = useStatus();
  return (
    <>
      <PageHeader
        title="Your workspace"
        description="Open a conversation or start a new one from the sidebar."
        actions={
          <Button render={<Link to="/setup" />}>
            {setup.data?.needed ? "Set up your instance" : "Check readiness"}
            <ArrowRight />
          </Button>
        }
      />
      <ErrorNotice error={setup.error || projects.error || sources.error} />
      <div className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>HARNESS UI</span>
          <h2>A workspace for your agents.</h2>
          <p>
            Configuration stays on your server. Collaborators share the same
            instance; your navigation and display preferences stay yours.
          </p>
        </div>
        <div className={styles.heroStats}>
          <strong>{projects.data?.length ?? "—"}</strong>
          <span>Projects</span>
          <strong>{sources.data?.sources.length ?? "—"}</strong>
          <span>Configurations</span>
        </div>
      </div>
      {setup.data?.needed && (
        <Panel title="Finish first-use setup">
          <p>
            No default agent is configured yet. Guided setup creates the initial
            model, agent and environment selections without hand-writing YAML.
          </p>
          <Link to="/setup">Start setup</Link>
        </Panel>
      )}
      <div className={styles.cardGrid}>
        <Link to="/projects" className={styles.projectCard}>
          <Folder size={24} />
          <strong>Choose a Project</strong>
          <p>Project folders and defaults for new conversations.</p>
        </Link>
        <Link to="/settings/accounts" className={styles.projectCard}>
          <PlugsConnected size={24} />
          <strong>Connect providers</strong>
          <p>Subscription accounts and securely referenced model keys.</p>
        </Link>
        <Link to="/settings" className={styles.projectCard}>
          <SlidersHorizontal size={24} />
          <strong>Workspace settings</strong>
          <p>Models, agents, environments, MCP and shared defaults.</p>
        </Link>
      </div>

      {status.data?.app.capability_warnings?.length ||
      status.data?.app.content_plugin_diagnostics?.length ? (
        <Panel title="Configuration diagnostics">
          {[
            ...(status.data?.app.capability_warnings ?? []),
            ...(status.data?.app.content_plugin_diagnostics ?? []),
          ].map((message) => (
            <p key={message}>{message}</p>
          ))}
        </Panel>
      ) : null}
      <p className={styles.muted}>
        Open a conversation from the sidebar or create one to start working.
        Nothing is sent until you choose Send.
      </p>
    </>
  );
}
