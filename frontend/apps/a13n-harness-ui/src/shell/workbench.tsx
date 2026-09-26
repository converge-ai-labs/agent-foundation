import { ModelsPage } from "../configuration/models";
import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useMatch,
  useNavigate,
} from "react-router";
import {
  Button,
  Logo,
  Wordmark,
  ChoiceField,
  ModalFrame,
  Sheet,
  SheetPopup,
  SheetTitle,
} from "a13n-ui";
import {
  Archive,
  Brain,
  House,
  Moon,
  Sun,
  List,
  Users,
  Gear,
} from "@phosphor-icons/react";
import { useSetup, useSources, useStatus } from "../transport/context";
import type { Schema } from "../transport/client";
import { AccountsPage } from "../setup/accounts";
import { SetupPage } from "../setup/setup";
import { wizardDismissed } from "../setup/wizard-state";
import { SourcesPage, SourcePage } from "../configuration/sources";
import {
  SettingsLayout,
  GeneralSettings,
  CapabilitiesPage,
  EnvironmentsPage,
} from "../configuration/settings";
import { ProjectsPage, ProjectPage } from "../configuration/projects";
import { ErrorNotice, Panel, TextField } from "./ui";
import { useLiveWorkbench, type Profile } from "./presence";
import { SharedPointers } from "./shared-pointers";
import styles from "./workbench.module.css";
import { ArchivedPage } from "../conversations/archived";
import { ConversationNavigation } from "../conversations/navigation";
import { ConversationPage } from "../conversations/conversation";
import { useThread } from "../conversations/queries";
import { MemoryNavigation, MemoryPage } from "../memory";
import { NewConversationPage } from "../conversations/new-conversation";
import { NativeWorkspace } from "../native/workspace";
import { ResultsProvider, useResults } from "../conversations/results";
import { LiveThreadsProvider } from "../conversations/live-threads";
import { UnsentProvider } from "../conversations/unsent";

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

export function Workbench(props: {
  status: Schema<"ListenerStatus">;
  forget: () => void;
  unauthorized: () => void;
}) {
  return (
    <ResultsProvider>
      <LiveThreadsProvider>
        <UnsentProvider>
          <WorkbenchContent {...props} />
        </UnsentProvider>
      </LiveThreadsProvider>
    </ResultsProvider>
  );
}

function WorkbenchContent({
  status: initialStatus,
  forget,
  unauthorized,
}: {
  status: Schema<"ListenerStatus">;
  forget: () => void;
  unauthorized: () => void;
}) {
  const results = useResults();
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
  const menuLocation = useRef("");
  const live = useLiveWorkbench(
    profile,
    !!status.features?.page_presence,
    unauthorized,
    nativeFocus,
  );
  const disconnected = live.summary === "Reconnecting";
  const location = useLocation();
  const selectedThreadId = useMatch("/threads/:threadId")?.params.threadId;
  const selectedThread = useThread(selectedThreadId ?? "");
  const memoryMode =
    location.pathname === "/memory" ||
    !!selectedThread.data?.thread.memory_scope;
  const memoryEnabled = !!status.app?.memory_organization?.memory_enabled;
  const navigate = useNavigate();
  useEffect(() => {
    if (
      location.pathname === "/" &&
      !location.search &&
      setup.data?.fresh &&
      setup.data.draft_scope &&
      !wizardDismissed(setup.data.draft_scope)
    ) {
      navigate("/setup", { replace: true });
    }
  }, [setup.data, location.pathname, location.search, navigate]);
  useEffect(() => {
    setMenu(false);
  }, [location.key]);
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
    { to: "/", label: "Home", icon: House },
    { to: "/archived", label: "Archived", icon: Archive },
    ...(memoryEnabled ? [{ to: "/memory", label: "Memory", icon: Brain }] : []),
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
      {memoryMode && memoryEnabled ? (
        <MemoryNavigation
          projectId={
            selectedThread.data?.thread.memory_scope
              ? selectedThread.data.thread.configuration.project_id
              : undefined
          }
        />
      ) : (
        <ConversationNavigation presence={live.presence} />
      )}
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
          <Link to="/" className={styles.brand} aria-label="Harness UI home">
            <Logo alt="" width={28} height={28} />
            <Wordmark />
          </Link>
          {themeToggle}
        </header>
        {navigation}
      </aside>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetPopup
          id="workbench-navigation"
          finalFocus={() => {
            if (
              location.key !== menuLocation.current &&
              new URLSearchParams(location.search).get("compose") === "1"
            )
              return (
                document.querySelector<HTMLElement>("[data-composer-editor]") ??
                mobileMenuButton.current
              );
            return mobileMenuButton.current;
          }}
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
          {(results.storageError || results.lookupError) && (
            <div role="alert" className={styles.notice}>
              <p>{results.storageError || results.lookupError}</p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void results.tracker?.refresh()}
              >
                Retry new-result tracking
              </Button>
            </div>
          )}
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
                onClick={() => {
                  menuLocation.current = location.key;
                  setMenu(true);
                }}
              >
                <List />
              </Button>
            }
            onFocus={setNativeFocus}
            unauthorized={unauthorized}
          >
            <Routes>
              <Route
                path="/memory"
                element={
                  <MemoryPage profile={profile} unauthorized={unauthorized} />
                }
              />
              <Route
                path="/"
                element={
                  <NewConversationPage
                    profile={profile}
                    unauthorized={unauthorized}
                  />
                }
              />
              <Route
                path="/new/:draftId?"
                element={
                  <NewConversationPage
                    profile={profile}
                    unauthorized={unauthorized}
                  />
                }
              />
              <Route
                path="/threads/:threadId"
                element={
                  <ConversationPage
                    profile={profile}
                    unauthorized={unauthorized}
                  />
                }
              />
              <Route path="/archived" element={<ArchivedPage />} />
              <Route element={<SettingsLayout />}>
                <Route path="/setup" element={<SetupPage />} />
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:projectId" element={<ProjectPage />} />
                <Route path="/settings" element={<GeneralSettings />} />
                <Route
                  path="/settings/notifications"
                  element={<NotificationSettings />}
                />
                <Route path="/settings/models" element={<ModelsPage />} />
                <Route
                  path="/settings/agents"
                  element={<SourcesPage kinds={["agent"]} title="Agents" />}
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
