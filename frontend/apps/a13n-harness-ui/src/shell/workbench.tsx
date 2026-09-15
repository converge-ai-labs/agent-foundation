import { useEffect, useRef, useState } from "react";
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
import { readWizardDraft, wizardDismissed } from "../setup/wizard-state";
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
import { SharedPointers } from "./shared-pointers";
import styles from "./workbench.module.css";
import { ConversationNavigation } from "../conversations/navigation";
import { ConversationPage } from "../conversations/conversation";
import { NativeWorkspace } from "../native/workspace";

import { pageLink } from "./page-links";
import { readPreference, writePreference } from "./preferences";
import { NotificationSettings } from "./notifications";
import { ConnectionNotice, ConnectionNoticeContext } from "./connection";

const profileColors = [
  { value: "#64748b", label: "Slate" },
  { value: "#2563eb", label: "Blue" },
  { value: "#7c3aed", label: "Purple" },
  { value: "#059669", label: "Green" },
  { value: "#d97706", label: "Amber" },
].map((color) => ({
  ...color,
  icon: (
    <span
      aria-hidden="true"
      className={styles.colorSwatch}
      style={{ backgroundColor: color.value }}
    />
  ),
}));

export function Workbench({
  status: initialStatus,
  forget,
  unauthorized,
}: {
  status: Schema<"ListenerStatus">;
  forget: () => void;
  unauthorized: () => void;
}) {
  const setup = useSetup();
  const statusQuery = useStatus();
  const status = statusQuery.data ?? initialStatus;
  const [theme, setTheme] = useState(() => readPreference("theme", "light"));
  const [profile, setProfile] = useState<Profile>(() => ({
    display_name:
      readPreference("display-name", "").trim() ||
      `Guest ${crypto.randomUUID().slice(0, 6)}`,
    color:
      readPreference("color", "") ||
      profileColors[Math.floor(Math.random() * profileColors.length)]!.value,
  }));
  const [nativeFocus, setNativeFocus] = useState<Schema<"PageTarget"> | null>(
    null,
  );
  const sources = useSources();
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [displayName, setDisplayName] = useState(profile.display_name);
  const openPeople = () => {
    setDisplayName(profile.display_name);
    setPeopleOpen(true);
  };
  const [menu, setMenu] = useState(false);
  const mobileMenuButton = useRef<HTMLButtonElement>(null);
  const live = useLiveWorkbench(
    profile,
    !!status.features?.page_presence,
    unauthorized,
    nativeFocus,
  );
  const disconnected = live.summary === "Reconnecting";
  const location = useLocation();
  const navigate = useNavigate();
  const { client } = useTransport();
  const [restoreTarget, setRestoreTarget] = useState(() =>
    location.pathname === "/" && !location.search
      ? readPreference("last-thread", "")
      : "",
  );
  useEffect(() => {
    if (
      location.pathname === "/" &&
      !location.search &&
      setup.data?.fresh &&
      setup.data.draft_scope &&
      !wizardDismissed(setup.data.draft_scope)
    ) {
      setRestoreTarget("");
      navigate("/setup", { replace: true });
    }
  }, [setup.data, location.pathname, location.search, navigate]);
  useEffect(() => {
    if (!restoreTarget || setup.isPending || setup.data?.fresh) return;
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
  }, [
    client,
    restoreTarget,
    location.pathname,
    navigate,
    setup.isPending,
    setup.data?.fresh,
  ]);
  useEffect(() => {
    setMenu(false);
  }, [location.pathname, location.search]);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    writePreference("theme", theme);
  }, [theme]);
  useEffect(() => {
    writePreference("display-name", profile.display_name);
    writePreference("color", profile.color);
  }, [profile]);
  const updateProfile = (next: Profile) => setProfile(next);
  const links = [
    { to: "/", label: "Overview", icon: House },
    { to: "/settings", label: "Settings", icon: Gear },
  ];
  const themeToggle = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={
        theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
      }
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
    >
      {theme === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
  const navigation = (
    <>
      <ConversationNavigation presence={live.presence} />
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
      <div className={styles.sidebarFooter}>
        <div className={styles.actions}>
          <Button variant="ghost" onClick={openPeople}>
            <Users />
            <span>
              {disconnected
                ? "People"
                : `${live.presence?.participants.length ?? 0} online`}
            </span>
          </Button>
          <Button variant="ghost" onClick={forget}>
            Log out
          </Button>
        </div>
        <small>
          {live.summary} · Version <span>{status.version}</span>
        </small>
      </div>
    </>
  );
  const content = (
    <div className={styles.shell}>
      <SharedPointers
        socket={live.socket}
        presence={live.presence}
        focus={live.focus}
      />
      <a className={styles.skipLink} href="#main-content">
        Skip to content
      </a>
      <aside className={styles.sidebar} aria-label="Workbench navigation">
        <header className={styles.sidebarHeader}>
          <Link to="/" className={styles.brand}>
            <Wordmark className={styles.brandMark} />
            <span>Harness UI</span>
          </Link>
          {themeToggle}
        </header>
        {navigation}
      </aside>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetPopup
          id="workbench-navigation"
          finalFocus={mobileMenuButton}
          side="left"
          className={styles.mobileNavigation}
          closeProps={{ "aria-label": "Close navigation" }}
        >
          <header className={styles.mobileNavigationHeader}>
            <SheetTitle>Harness UI</SheetTitle>
            {themeToggle}
          </header>
          {navigation}
        </SheetPopup>
      </Sheet>
      <div className={styles.workspace}>
        <main id="main-content" className={styles.main}>
          {disconnected && <ConnectionNotice retry={live.retrySummary} />}
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
            profile={
              <Button
                variant="ghost"
                size="sm"
                className={styles.profileButton}
                title={`${profile.display_name} · Edit collaboration name`}
                aria-label={`Your collaboration name: ${profile.display_name}`}
                onClick={openPeople}
              >
                <span
                  className={styles.profileAvatar}
                  style={{ backgroundColor: profile.color }}
                  aria-hidden="true"
                >
                  {Array.from(profile.display_name)[0]?.toUpperCase()}
                </span>
                <span className={styles.profileName}>
                  {profile.display_name}
                </span>
              </Button>
            }
            navigation={
              <Button
                ref={mobileMenuButton}
                variant="ghost"
                size="icon"
                className={styles.mobileMenu}
                aria-label="Open navigation"
                aria-haspopup="dialog"
                aria-expanded={menu}
                aria-controls={menu ? "workbench-navigation" : undefined}
                onClick={() => setMenu(true)}
              >
                <List />
              </Button>
            }
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
                <Route path="/projects/:projectId" element={<ProjectPage />} />
                <Route path="/settings" element={<GeneralSettings />} />
                <Route
                  path="/settings/notifications"
                  element={<NotificationSettings />}
                />
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
          <form
            className={styles.profileForm}
            onSubmit={(event) => {
              event.preventDefault();
              if (
                !displayName.trim() ||
                Array.from(displayName.trim()).length > 80
              )
                return;
              updateProfile({ ...profile, display_name: displayName.trim() });
              setPeopleOpen(false);
            }}
          >
            <TextField
              label="Your display name"
              value={displayName}
              onChange={setDisplayName}
            />
            <Button
              type="submit"
              variant="outline"
              disabled={
                !displayName.trim() ||
                Array.from(displayName.trim()).length > 80
              }
            >
              Save name
            </Button>
          </form>
          <ChoiceField
            label="Your color"
            value={profile.color}
            onValueChange={(color) => updateProfile({ ...profile, color })}
            options={profileColors}
          />
          <p>Presence: {live.presenceState}</p>
          <details>
            <summary>Instance information</summary>
            <p>
              Version {status.version} · {status.app?.state ?? "Connected"}
            </p>
            <p>
              {status.features?.host_files
                ? "Native computer sharing is enabled on the server."
                : "Native sharing is disabled by this server. Start without --no-share-computer to enable it."}
            </p>
            {status.features?.host_files && !status.features?.host_git && (
              <p>Git is unavailable on this server. Files remains available.</p>
            )}
            {status.features?.host_files && !status.features?.host_terminal && (
              <p>
                Native PTY is unavailable on this server; it requires POSIX
                support.
              </p>
            )}
          </details>
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
  );
  return (
    <ConnectionNoticeContext.Provider value={disconnected}>
      {content}
    </ConnectionNoticeContext.Provider>
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
      {(setup.data?.needed ||
        (setup.data?.draft_scope &&
          readWizardDraft(setup.data.draft_scope))) && (
        <Panel
          title={
            setup.data?.fresh
              ? "Finish first-use setup"
              : "Setup needs attention"
          }
        >
          <p>
            {setup.data?.fresh
              ? "Connect a model and choose where it can work. You can start without a Project."
              : "Review your saved setup or repair the existing configuration without replacing your resources."}
          </p>
          <Link to="/setup">Continue setup</Link>
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
