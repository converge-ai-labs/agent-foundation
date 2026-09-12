import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
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
  SquaresFour,
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
  ProjectsPage,
  ProjectPage,
  ReadinessPreview,
} from "../configuration/projects";
import { ErrorNotice, PageHeader, Panel, TextField } from "./ui";
import { useLiveWorkbench, type Profile } from "./presence";
import styles from "./workbench.module.css";
import { ConversationNavigation } from "../conversations/navigation";
import { ConversationPage } from "../conversations/conversation";

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
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const live = useLiveWorkbench(
    profile,
    !!status.features?.page_presence,
    unauthorized,
  );
  const location = useLocation();
  const navigate = useNavigate();
  const { client } = useTransport();
  const [restoreTarget, setRestoreTarget] = useState(() =>
    location.pathname === "/" ? readPreference("last-thread", "") : "",
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
    { to: "/", label: "Workbench", icon: House },
    { to: "/projects", label: "Projects", icon: Folder },
    { to: "/settings/resources", label: "Resources", icon: SlidersHorizontal },
    {
      to: "/settings/accounts",
      label: "Provider accounts",
      icon: PlugsConnected,
    },
    {
      to: "/settings/catalog",
      label: "Implementation catalog",
      icon: SquaresFour,
    },
    { to: "/setup", label: "Setup & readiness", icon: Gear },
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
            <span className={styles.brandMark}>a13n</span>
            <strong>Harness UI</strong>
          </Link>
          <div className={styles.sidebarCaption}>YOUR WORKSPACE</div>
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
                Forget API key
              </Button>
            </div>
          </header>
          <main
            id="main-content"
            className={`${styles.main} ${location.pathname.startsWith("/threads/") ? styles.conversationMain : ""}`}
          >
            {status.access === "dangerous_bypass" && (
              <div className={styles.notice}>
                Instance authentication is disabled by the server's explicit
                dangerous bypass setting.
              </div>
            )}
            {status.app?.candidate_error_message && (
              <div role="alert" className={styles.notice}>
                <strong>Configuration candidate rejected</strong>
                <p>{status.app.candidate_error_message}</p>
                <p>
                  The previous accepted generation remains active. Source
                  inspection shows accepted content, not the invalid disk
                  candidate.
                </p>
              </div>
            )}
            <ErrorNotice
              error={statusQuery.error}
              retry={() => void statusQuery.refetch()}
            />
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
              <Route path="/setup" element={<SetupPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:projectId" element={<ProjectPage />} />
              <Route path="/settings" element={<SourcesPage />} />
              <Route path="/settings/resources" element={<SourcesPage />} />
              <Route path="/settings/source" element={<SourcePage />} />
              <Route path="/settings/accounts" element={<AccountsPage />} />
              <Route path="/settings/catalog" element={<CatalogPage />} />
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
          </main>
        </div>
        <ModalFrame
          open={peopleOpen}
          onOpenChange={setPeopleOpen}
          title="People on this instance"
          description="Presence is per browser tab and resets when the server restarts. Display profiles are not authenticated identities or provider accounts."
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
                <span>
                  {participant.foreground ? "Foreground" : "Background"}
                </span>
                <small>
                  {live.presence?.same_page_participant_ids?.includes(
                    participant.participant_id,
                  )
                    ? "Same page"
                    : participant.availability}
                </small>
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
        title="Your workbench"
        description="Prepare your workspace, connect providers, and choose how your agents work."
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
          <span>Sources</span>
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
          <p>Named host directories and defaults for new threads.</p>
        </Link>
        <Link to="/settings/accounts" className={styles.projectCard}>
          <PlugsConnected size={24} />
          <strong>Connect providers</strong>
          <p>Subscription accounts and securely referenced model keys.</p>
        </Link>
        <Link to="/settings/resources" className={styles.projectCard}>
          <SlidersHorizontal size={24} />
          <strong>Configure resources</strong>
          <p>Models, agents, environments, MCP and shared defaults.</p>
        </Link>
      </div>
      {!setup.data?.needed && <ReadinessPreview />}
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
        Opening this workbench does not start a Run.
      </p>
    </>
  );
}
function CatalogPage() {
  const { client } = useTransport();
  const [search, setSearch] = useState("");
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  return (
    <>
      <PageHeader
        title="Implementation catalog"
        description="Installed implementations are available to configure, not automatically enabled for an agent."
      />
      <ErrorNotice error={catalog.error} retry={() => void catalog.refetch()} />
      <TextField
        label="Find implementations"
        value={search}
        onChange={setSearch}
      />
      <div className={styles.resourceList}>
        {catalog.data
          ?.filter((item) => `${item.kind} ${item.key}`.includes(search))
          .map((item) => (
            <div
              className={styles.resourceRow}
              key={`${item.kind}:${item.key}`}
            >
              <div>
                <strong>{item.key}</strong>
                <small>{item.kind.replaceAll("_", " ")}</small>
              </div>
              <span>{item.source}</span>
              <small>
                {item.distribution_name} {item.distribution_version}
              </small>
            </div>
          ))}
      </div>
      <p>
        Content-plugin installation is managed outside this HTTP surface. Its
        diagnostics appear on the workbench; it is not a Harness plugin
        resource.
      </p>
    </>
  );
}
