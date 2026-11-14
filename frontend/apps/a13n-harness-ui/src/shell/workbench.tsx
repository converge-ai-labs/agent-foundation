import { useAppearance } from "../appearance/react";
import { BotComposerProvider, BotNavigation, BotPage } from "../bot";
import { AppearanceSettings } from "../configuration/appearance";
import { useTranslation } from "../i18n";
import { UsagePage } from "../usage/page";
import { ModelsPage } from "../configuration/models";
import { VoicePage, VoiceIndicator, useVoiceSettings } from "../voice";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Link,
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
import { ChatSearchProvider } from "../conversations/chat-search";
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
import { WorkbenchSidebar, MobileNavigation } from "./sidebar";
import { fitVisualViewport } from "./visual-viewport";
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
          <BotComposerProvider>
            <ChatSearchProvider>
              <WorkbenchContent {...props} />
            </ChatSearchProvider>
          </BotComposerProvider>
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
  const { t } = useTranslation();
  const results = useResults();
  const debug = useDebug();
  const setup = useSetup();
  const voiceSettings = useVoiceSettings();
  const statusQuery = useStatus();
  const status = statusQuery.data ?? initialStatus;
  const { dark, store: appearance } = useAppearance();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => readPreference("sidebar", "expanded") === "collapsed",
  );
  const shellRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const workspaceLeft = useRef<number | null>(null);
  const workspaceMotion = useRef<Animation | null>(null);
  const changeSidebar = (collapsed: boolean) => {
    workspaceLeft.current =
      workspaceRef.current?.getBoundingClientRect().left ?? null;
    setSidebarCollapsed(collapsed);
  };
  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    const previous = workspaceLeft.current;
    workspaceLeft.current = null;
    workspaceMotion.current?.cancel();
    if (
      !workspace ||
      previous === null ||
      !workspace.animate ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const distance = previous - workspace.getBoundingClientRect().left;
    const tokens = getComputedStyle(workspace);
    workspaceMotion.current = workspace.animate(
      [
        { transform: `translateX(${distance}px)` },
        { transform: "translateX(0)" },
      ],
      {
        duration:
          parseFloat(tokens.getPropertyValue("--a13n-duration-slow")) || 320,
        easing:
          tokens.getPropertyValue("--a13n-ease-sheet").trim() || "ease-out",
      },
    );
  }, [sidebarCollapsed]);
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
  const voicePage = location.pathname === "/live";
  useEffect(() => {
    if (shellRef.current) return fitVisualViewport(shellRef.current);
  }, [voicePage]);
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
      location.pathname === "/bot" ||
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
    writePreference("display-name", profile.display_name);
    writePreference("color", profile.color);
  }, [profile]);
  useEffect(() => {
    writePreference("sidebar", sidebarCollapsed ? "collapsed" : "expanded");
  }, [sidebarCollapsed]);
  const updateProfile = (next: Profile) => setProfile(next);
  const navigation = (
    <>
      <div hidden={settingsMode} className={styles.navigationBody}>
        {memoryMode && memoryEnabled ? (
          <>
            <BotNavigation />
            <MemoryNavigation
              backTo={lastChat.current}
              projectId={
                selectedThread.data?.thread.memory_scope
                  ? selectedThread.data.thread.configuration.project_id
                  : undefined
              }
            />
          </>
        ) : (
          <ConversationNavigation />
        )}
      </div>
      {settingsMode && <SettingsNavigation backTo={lastChat.current} />}
      <div className={styles.sidebarFooter}>
        <Menu>
          <MenuTrigger
            className={styles.workspaceMenu}
            aria-label={t("Workspace menu")}
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
              <small>
                {disconnected ? t("Reconnecting…") : t("Your workspace")}
              </small>
            </span>
            <CaretUpDown size={16} aria-hidden="true" />
          </MenuTrigger>
          <MenuPopup side="top" align="start" className={styles.workspacePopup}>
            <MenuItem render={<Link to="/settings" />}>
              <Gear />
              {t("Settings")}
            </MenuItem>
            {voiceSettings.data?.enabled && (
              <MenuItem render={<Link to="/live" />}>
                <Waveform />
                {t("Live")}
              </MenuItem>
            )}
            {memoryEnabled && (
              <MenuItem render={<Link to="/memory" />}>
                <Brain />
                {t("Memory")}
              </MenuItem>
            )}
            <MenuSeparator />
            <MenuItem onClick={openPeople}>
              <Users />
              {t("People & profile")}
            </MenuItem>
            <MenuItem render={<Link to="/usage" />}>
              <ChartBar />
              {t("Usage")}
            </MenuItem>
            <MenuItem render={<Link to="/archived" />}>
              <Archive />
              {t("Archived chats")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              onClick={() => appearance.setTheme(dark ? "light" : "dark")}
            >
              {dark ? <Sun /> : <Moon />}
              {dark ? t("Light appearance") : t("Dark appearance")}
            </MenuItem>
            <MenuCheckboxItem
              checked={debug.enabled}
              onCheckedChange={debug.setEnabled}
              variant="switch"
            >
              <span className={styles.debugMenuLabel}>
                <Bug />
                {t("Debug mode")}
              </span>
            </MenuCheckboxItem>
            <MenuSeparator />
            <MenuItem variant="destructive" onClick={forget}>
              <SignOut />
              {t("Log out")}
            </MenuItem>
            <span className={styles.version}>
              Harness UI - {status.version}
            </span>
          </MenuPopup>
        </Menu>
      </div>
    </>
  );
  if (location.pathname === "/live") return <VoicePage />;
  const content = (
    <div ref={shellRef} className={styles.shell}>
      <SharedPointers
        socket={live.socket}
        presence={live.presence}
        focus={live.focus}
      />
      <a className={styles.skipLink} href="#main-content">
        {t("Skip to content")}
      </a>
      <WorkbenchSidebar collapsed={sidebarCollapsed}>
        <header className={styles.sidebarHeader}>
          <Link
            to="/"
            className={styles.brand}
            aria-label={t("Harness UI home")}
          >
            <Logo alt="" width={20} height={20} />
            <span>Harness</span>
          </Link>
          <div className={styles.headerActions}>
            <Button
              ref={collapseButton}
              variant="ghost"
              size="icon-sm"
              aria-label={t("Collapse navigation")}
              title={t("Collapse navigation")}
              onClick={() => {
                changeSidebar(true);
                requestAnimationFrame(() => expandButton.current?.focus());
              }}
            >
              <SidebarSimple />
            </Button>
          </div>
        </header>
        {navigation}
      </WorkbenchSidebar>
      <MobileNavigation
        open={menu}
        onOpenChange={setMenu}
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
      >
        {navigation}
      </MobileNavigation>
      <div ref={workspaceRef} className={styles.workspace}>
        <main id="main-content" tabIndex={-1} className={styles.main}>
          <div className={styles.workbenchFeedback}>
            {disconnected && <ConnectionNotice retry={live.retrySummary} />}
            {(results.mutationError ||
              (!disconnected && results.lookupError)) && (
              <div role="alert" className={styles.notice}>
                <p>{results.mutationError || results.lookupError}</p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void results.tracker?.refresh()}
                >
                  {t("Retry updates")}
                </Button>
              </div>
            )}
            {status.access === "dangerous_bypass" && (
              <div className={styles.notice}>
                {t(
                  "Server login is disabled. Anyone who can reach this server can use it.",
                )}
              </div>
            )}
            {status.app?.candidate_error_message && (
              <div role="alert" className={styles.notice}>
                <details>
                  <summary>
                    {t(
                      "Settings couldn't be updated. Previous settings are still active.",
                    )}
                  </summary>
                  <p>{status.app.candidate_error_message}</p>
                </details>
                <Button
                  variant="ghost"
                  size="sm"
                  render={<Link to="/settings" />}
                >
                  {t("Settings")}
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
            profileMenu={
              <MenuItem onClick={openPeople}>
                <Users />
                {t("People & profile")}
              </MenuItem>
            }
            profile={
              <>
                <VoiceIndicator />
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
                  threadTitle={settingsMode ? "" : t("this page")}
                />
                {debug.enabled && (
                  <button
                    type="button"
                    className={styles.debugIndicator}
                    onClick={() => debug.setEnabled(false)}
                    title={t("Turn off Debug mode")}
                  >
                    <Bug size={14} />
                    {t("Debug")}
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
                    aria-label={t("Expand navigation")}
                    title={t("Expand navigation")}
                    onClick={() => {
                      changeSidebar(false);
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
                  aria-label={t("Open navigation")}
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
                path="/bot"
                element={
                  <BotPage profile={profile} unauthorized={unauthorized} />
                }
              />
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
                    <p role="status">{t("Checking setup\u2026")}</p>
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
                <Route
                  path="/settings"
                  element={
                    location.hash === "#browser" ? (
                      <Navigate
                        to="/settings/environments?tab=browser"
                        replace
                      />
                    ) : (
                      <GeneralSettings />
                    )
                  }
                />
                <Route
                  path="/settings/appearance"
                  element={<AppearanceSettings />}
                />
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
                <Route
                  path="/settings/resources"
                  element={
                    location.hash === "#browser" ? (
                      <Navigate
                        to="/settings/environments?tab=browser"
                        replace
                      />
                    ) : (
                      <SourcesPage />
                    )
                  }
                />
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
                    <h2>{t("Page not found")}</h2>
                    <p>
                      {t("This address doesn't match a page in Harness UI.")}
                    </p>
                    <Button variant="outline" render={<Link to="/" />}>
                      {t("Go to home")}
                    </Button>
                  </div>
                }
              />
            </Routes>
          </NativeWorkspace>
        </main>
      </div>
      <ModalFrame
        bottomStickOnMobile
        open={peopleOpen}
        onOpenChange={setPeopleOpen}
        title={t("People in this workspace")}
        closeLabel={t("Close")}
        footer={
          <Button variant="outline" onClick={() => setPeopleOpen(false)}>
            {t("Close")}
          </Button>
        }
      >
        <div className={styles.stack}>
          <SettingsSection title={t("Your profile")}>
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
              <SettingsRow label={t("Name")} controlId="workspace-display-name">
                <div className={styles.profileNameControl}>
                  <Input
                    aria-label={t("Your display name")}
                    id="workspace-display-name"
                    autoFocus
                    onFocus={(event) => event.currentTarget.select()}
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                  <Button
                    aria-label={t("Save name")}
                    type="submit"
                    variant="outline"
                    disabled={
                      !displayName.trim() ||
                      Array.from(displayName.trim()).length > 80
                    }
                  >
                    {t("Save")}
                  </Button>
                </div>
              </SettingsRow>
            </form>
            <ChoiceField
              label={t("Color")}
              value={profile.color}
              onValueChange={(color) => updateProfile({ ...profile, color })}
              options={profileColors.map((color) => ({
                ...color,
                label: t(color.label),
              }))}
            />
            <SettingsRow label={t("Presence")}>
              <span>{t(live.presenceState)}</span>
            </SettingsRow>
          </SettingsSection>
          <details className={styles.details}>
            <summary>{t("About this instance")}</summary>
            <p>
              {t("Version")} {status.version} -{" "}
              {status.app?.state ?? t("Connected")}
            </p>
            <p>
              {t(
                status.features?.host_files
                  ? "Computer sharing is on for this server."
                  : "Computer sharing is off. Restart the server without --no-share-computer to turn it on.",
              )}
            </p>
            {status.features?.host_files && !status.features?.host_git && (
              <p>
                {t(
                  "Git is unavailable on this server. Files remains available.",
                )}
              </p>
            )}
            {status.features?.host_files && !status.features?.host_terminal && (
              <p>
                {t(
                  "Terminals aren't supported on this server. POSIX support is required.",
                )}
              </p>
            )}
          </details>
          <SettingsSection title={t("People")}>
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
                      {participant.display_name || t("Anonymous")}
                      {own && (
                        <span className={styles.personYou}> - {t("You")}</span>
                      )}
                    </strong>
                    <span>
                      {t(participant.foreground ? "Active" : "Away")}
                      {samePage ? ` - ${t("This page")}` : ""}
                    </span>
                    {participant.unavailable_reason && (
                      <span>{participant.unavailable_reason}</span>
                    )}
                  </div>
                  {href && !own && !samePage && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={t("Open page")}
                      render={<Link to={href} />}
                      onClick={() => setPeopleOpen(false)}
                    >
                      {t("Open")}
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
