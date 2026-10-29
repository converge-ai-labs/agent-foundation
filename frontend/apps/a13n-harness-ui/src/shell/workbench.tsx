import { UsagePage } from "../usage/page";
import { ShareButton } from "../sharing";
import { ModelsPage } from "../configuration/models";
import { VoicePage, VoiceIndicator, useVoiceSettings } from "../voice";
import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useMatch,
} from "react-router";
import {
  Button,
  Logo,
  Input,
  ModalFrame,
  Sheet,
  SheetPopup,
  SheetTitle,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  MenuSeparator,
  MenuCheckboxItem,
} from "a13n-ui";
import {
  Archive,
  ChartBar,
  Waveform,
  Brain,
  SidebarSimple,
  Moon,
  SignOut,
  Sun,
  List,
  Users,
  Gear,
  Compass,
  CaretUpDown,
  Bug,
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
  ConnectionsPage,
  AgentsPage,
  EnvironmentsPage,
} from "../configuration/settings";
import { SettingsNavigation } from "../configuration/settings-navigation";
import { ProjectsPage, ProjectPage } from "../configuration/projects";
import { ErrorNotice } from "./ui";
import {
  SettingsChoice as ChoiceField,
  SettingsRow,
  SettingsSection,
} from "./settings";
import { useLiveWorkbench, type Profile } from "./presence";
import { SharedPointers } from "./shared-pointers";
import { ParticipantAvatars, ParticipantAvatar } from "./participant-avatars";
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

import { useDebug } from "./debug";
import { pageLink } from "./page-links";
import { readPreference, writePreference } from "./preferences";
import { WorkbenchSidebar } from "./sidebar";
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
  const debug = useDebug();
  const setup = useSetup();
  const voiceSettings = useVoiceSettings();
  const statusQuery = useStatus();
  const status = statusQuery.data ?? initialStatus;
  const [theme, setTheme] = useState(() => readPreference("theme", "light"));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => readPreference("sidebar", "expanded") === "collapsed",
  );
  const collapseButton = useRef<HTMLButtonElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
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
    setMenu(false);
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
  const settingsMode = /^\/(settings|setup)(\/|$)/.test(location.pathname);
  const lastChat = useRef("/");
  const selectedThreadId = useMatch("/threads/:threadId")?.params.threadId;
  const selectedThread = useThread(selectedThreadId ?? "");
  const memoryMode =
    location.pathname === "/memory" ||
    !!selectedThread.data?.thread.memory_scope;
  if (
    !settingsMode &&
    !memoryMode &&
    (location.pathname === "/" ||
      location.pathname === "/new" ||
      (selectedThread.data && location.pathname.startsWith("/threads/")))
  ) {
    lastChat.current = `${location.pathname}${location.search}`;
  }
  const memoryEnabled = !!status.app?.memory_organization?.memory_enabled;
  // Decide first-use routing before mounting the conversation, whose own
  // effect normalizes the empty root URL to a draft route.
  const firstUseEntry = location.pathname === "/" && !location.search;
  const enterSetup =
    firstUseEntry &&
    setup.data?.fresh &&
    setup.data.draft_scope &&
    !wizardDismissed(setup.data.draft_scope);
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
  useEffect(() => {
    writePreference("sidebar", sidebarCollapsed ? "collapsed" : "expanded");
  }, [sidebarCollapsed]);
  const updateProfile = (next: Profile) => setProfile(next);
  const links = [
    ...(voiceSettings.data?.enabled
      ? [{ to: "/live", label: "Live", icon: Waveform }]
      : []),
  ];
  const navigation = (
    <>
      <div hidden={settingsMode} className={styles.navigationBody}>
        {memoryMode && memoryEnabled ? (
          <MemoryNavigation
            backTo={lastChat.current}
            projectId={
              selectedThread.data?.thread.memory_scope
                ? selectedThread.data.thread.configuration.project_id
                : undefined
            }
          />
        ) : (
          <ConversationNavigation />
        )}
        {links.length > 0 && (
          <nav
            aria-label="Workbench shortcuts"
            className={styles.supportingNav}
          >
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
        )}
      </div>
      {settingsMode && <SettingsNavigation backTo={lastChat.current} />}
      <div className={styles.sidebarFooter}>
        <Menu>
          <MenuTrigger
            className={styles.workspaceMenu}
            aria-label="Workspace menu"
          >
            <span
              className={styles.profileAvatar}
              style={{ backgroundColor: profile.color }}
              aria-hidden="true"
            >
              {Array.from(profile.display_name)[0]?.toUpperCase()}
            </span>
            <span className={styles.workspacePerson}>
              <strong>{profile.display_name}</strong>
              <small>{disconnected ? "Reconnecting…" : "Your workspace"}</small>
            </span>
            <CaretUpDown size={16} aria-hidden="true" />
          </MenuTrigger>
          <MenuPopup side="top" align="start" className={styles.workspacePopup}>
            <MenuItem render={<Link to="/settings" />}>
              <Gear />
              Settings
            </MenuItem>
            {memoryEnabled && (
              <MenuItem render={<Link to="/memory" />}>
                <Brain />
                Memory
              </MenuItem>
            )}
            <MenuSeparator />
            <MenuItem onClick={openPeople}>
              <Users />
              People & profile
            </MenuItem>
            <MenuItem render={<Link to="/usage" />}>
              <ChartBar />
              Usage
            </MenuItem>
            <MenuItem render={<Link to="/archived" />}>
              <Archive />
              Archived chats
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun /> : <Moon />}
              {theme === "dark" ? "Light appearance" : "Dark appearance"}
            </MenuItem>
            <MenuCheckboxItem
              checked={debug.enabled}
              onCheckedChange={debug.setEnabled}
              variant="switch"
            >
              <span className={styles.debugMenuLabel}>
                <Bug />
                Debug mode
              </span>
            </MenuCheckboxItem>
            <MenuSeparator />
            <MenuItem onClick={forget}>
              <SignOut />
              Log out
            </MenuItem>
            <span className={styles.version}>
              Harness UI · {status.version}
            </span>
          </MenuPopup>
        </Menu>
      </div>
    </>
  );
  if (location.pathname === "/live") return <VoicePage />;
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
      <WorkbenchSidebar collapsed={sidebarCollapsed}>
        <header className={styles.sidebarHeader}>
          <Link to="/" className={styles.brand} aria-label="Harness UI home">
            <Logo alt="" width={20} height={20} />
            <span>Harness</span>
          </Link>
          <div className={styles.headerActions}>
            <Button
              ref={collapseButton}
              variant="ghost"
              size="icon-sm"
              aria-label="Collapse navigation"
              title="Collapse navigation"
              onClick={() => {
                setSidebarCollapsed(true);
                requestAnimationFrame(() => expandButton.current?.focus());
              }}
            >
              <SidebarSimple />
            </Button>
          </div>
        </header>
        {navigation}
      </WorkbenchSidebar>
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
          </header>
          {navigation}
        </SheetPopup>
      </Sheet>
      <div className={styles.workspace}>
        <main id="main-content" tabIndex={-1} className={styles.main}>
          <div className={styles.workbenchFeedback}>
            {disconnected && <ConnectionNotice retry={live.retrySummary} />}
            {(results.storageError || results.lookupError) && (
              <div role="alert" className={styles.notice}>
                <p>{results.storageError || results.lookupError}</p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void results.tracker?.refresh()}
                >
                  Retry updates
                </Button>
              </div>
            )}
            {status.access === "dangerous_bypass" && (
              <div className={styles.notice}>
                Server login is disabled. Anyone who can reach this server can
                use it.
              </div>
            )}
            {status.app?.candidate_error_message && (
              <div role="alert" className={styles.notice}>
                <details>
                  <summary>
                    Settings couldn't be updated. Previous settings are still
                    active.
                  </summary>
                  <p>{status.app.candidate_error_message}</p>
                </details>
                <Button
                  variant="ghost"
                  size="sm"
                  render={<Link to="/settings" />}
                >
                  Settings
                </Button>
              </div>
            )}
            <ErrorNotice
              inline
              error={statusQuery.error}
              retry={() => void statusQuery.refetch()}
            />
          </div>
          <NativeWorkspace
            profile={
              <>
                <VoiceIndicator />
                <ShareButton />
                <ParticipantAvatars
                  participants={(live.presence?.participants ?? []).filter(
                    (participant) =>
                      !live.presence?.closed &&
                      participant.participant_id !==
                        live.presence?.participant_id &&
                      live.presence?.same_page_participant_ids?.includes(
                        participant.participant_id,
                      ),
                  )}
                  ownId={live.presence?.participant_id}
                  threadTitle="this page"
                />
                {debug.enabled && (
                  <button
                    type="button"
                    className={styles.debugIndicator}
                    onClick={() => debug.setEnabled(false)}
                    title="Turn off Debug mode"
                  >
                    <Bug size={14} />
                    Debug
                  </button>
                )}
              </>
            }
            navigation={
              <>
                {sidebarCollapsed && (
                  <Button
                    ref={expandButton}
                    variant="ghost"
                    size="icon"
                    className={styles.expandNavigation}
                    aria-label="Expand navigation"
                    title="Expand navigation"
                    onClick={() => {
                      setSidebarCollapsed(false);
                      requestAnimationFrame(() =>
                        collapseButton.current?.focus(),
                      );
                    }}
                  >
                    <SidebarSimple />
                  </Button>
                )}
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
              </>
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
                  firstUseEntry && setup.isPending ? (
                    <p role="status">Checking setup…</p>
                  ) : enterSetup ? (
                    <Navigate to="/setup" replace />
                  ) : (
                    <NewConversationPage
                      profile={profile}
                      unauthorized={unauthorized}
                    />
                  )
                }
              />
              <Route
                path="/new"
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
              <Route path="/usage" element={<UsagePage />} />
              <Route path="/archived" element={<ArchivedPage />} />
              <Route element={<SettingsLayout />}>
                <Route path="/setup" element={<SetupPage />} />
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:projectId" element={<ProjectPage />} />
                <Route path="/settings" element={<GeneralSettings />} />
                <Route
                  path="/settings/notifications"
                  element={<Navigate to="/settings#notifications" replace />}
                />
                <Route path="/settings/models" element={<ModelsPage />} />
                <Route path="/settings/agents" element={<AgentsPage />} />
                <Route
                  path="/settings/capabilities"
                  element={<Navigate to="/settings/agents" replace />}
                />
                <Route
                  path="/settings/environments"
                  element={<EnvironmentsPage />}
                />
                <Route
                  path="/settings/runtime"
                  element={
                    <Navigate to="/settings/environments?tab=runtime" replace />
                  }
                />
                <Route
                  path="/settings/envd"
                  element={
                    <Navigate to="/settings/environments?tab=runtime" replace />
                  }
                />
                <Route
                  path="/settings/connections"
                  element={<ConnectionsPage />}
                />
                <Route path="/settings/resources" element={<SourcesPage />} />
                <Route path="/settings/source" element={<SourcePage />} />
                <Route path="/settings/accounts" element={<AccountsPage />} />
                <Route
                  path="/settings/catalog"
                  element={<Navigate to="/settings/agents" replace />}
                />
              </Route>
              <Route
                path="*"
                element={
                  <div className={styles.notFound}>
                    <Compass size={32} aria-hidden="true" />
                    <h2>Page not found</h2>
                    <p>This address doesn't match a page in Harness UI.</p>
                    <Button variant="outline" render={<Link to="/" />}>
                      Go to home
                    </Button>
                  </div>
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
          <SettingsSection title="Your profile">
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
              <SettingsRow label="Name" controlId="workspace-display-name">
                <div className={styles.profileNameControl}>
                  <Input
                    aria-label="Your display name"
                    id="workspace-display-name"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                  <Button
                    aria-label="Save name"
                    type="submit"
                    variant="outline"
                    disabled={
                      !displayName.trim() ||
                      Array.from(displayName.trim()).length > 80
                    }
                  >
                    Save
                  </Button>
                </div>
              </SettingsRow>
            </form>
            <ChoiceField
              label="Color"
              value={profile.color}
              onValueChange={(color) => updateProfile({ ...profile, color })}
              options={profileColors}
            />
            <SettingsRow label="Presence">
              <span>{live.presenceState}</span>
            </SettingsRow>
          </SettingsSection>
          <details className={styles.details}>
            <summary>About this instance</summary>
            <p>
              Version {status.version} · {status.app?.state ?? "Connected"}
            </p>
            <p>
              {status.features?.host_files
                ? "Computer sharing is on for this server."
                : "Computer sharing is off. Restart the server without --no-share-computer to turn it on."}
            </p>
            {status.features?.host_files && !status.features?.host_git && (
              <p>Git is unavailable on this server. Files remains available.</p>
            )}
            {status.features?.host_files && !status.features?.host_terminal && (
              <p>
                Terminals aren't supported on this server. POSIX support is
                required.
              </p>
            )}
          </details>
          <SettingsSection title="People">
            {live.presence?.participants.map((participant) => {
              const own =
                participant.participant_id === live.presence?.participant_id;
              const samePage =
                live.presence?.same_page_participant_ids?.includes(
                  participant.participant_id,
                );
              const href =
                participant.focus && participant.availability === "available"
                  ? pageLink(participant.focus, sources.data?.sources)
                  : undefined;
              return (
                <div
                  className={styles.personRow}
                  key={participant.participant_id}
                >
                  <ParticipantAvatar participant={participant} />
                  <div className={styles.personInfo}>
                    <strong>
                      {participant.display_name || "Anonymous"}
                      {own && <span className={styles.personYou}> · You</span>}
                    </strong>
                    <span>
                      {participant.foreground ? "Active" : "Away"}
                      {samePage ? " · This page" : ""}
                    </span>
                    {participant.unavailable_reason && (
                      <span>{participant.unavailable_reason}</span>
                    )}
                  </div>
                  {href && !own && !samePage && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label="Open page"
                      render={<Link to={href} />}
                      onClick={() => setPeopleOpen(false)}
                    >
                      Open
                    </Button>
                  )}
                </div>
              );
            })}
          </SettingsSection>
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
