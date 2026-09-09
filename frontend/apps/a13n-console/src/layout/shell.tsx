import { Suspense, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import {
  type LucideIcon,
  Bot,
  MessagesSquare,
  Boxes,
  Sparkles,
  File,
  Monitor,
  Cable,
  Plug,
  Network,
  Activity,
  Settings,
  ChevronsUpDown,
  LogOut,
  Menu as MenuIcon,
  X,
  ChevronDown,
} from "lucide-react";
import { Button, Logo, Wordmark, Menu } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/context";
import { useWorkspace } from "./workspace";
import { ErrorNotice, Loading } from "../shared/feedback";
import styles from "./shell.module.css";

export function Shell() {
  const { t } = useTranslation(),
    auth = useAuth(),
    context = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    location = useLocation();
  const [open, setOpen] = useState(false);
  const contextual =
    /^\/workspaces\/[^/]+\/(sessions|settings)(\/|$)/.test(location.pathname) ||
    location.pathname === "/settings/profile" ||
    location.pathname === "/organization/settings";
  const user = auth.data!.user.value;
  const base = `/workspaces/${context.workspace.id}`;
  const groups: {
    label: string;
    entries: [string, string, LucideIcon, [string, string][]?][];
  }[] = [
    {
      label: "",
      entries: [
        ["agents", "Agents", Bot],
        ["sessions", "Sessions", MessagesSquare],
      ],
    },
    {
      label: "Resources",
      entries: [
        [
          "models",
          "Models",
          Boxes,
          [
            ["models", "All models"],
            ["models/providers", "Providers"],
          ],
        ],
        ["skills", "Skills", Sparkles],
        ["assets", "Assets", File],
        [
          "environments",
          "Environments",
          Monitor,
          [
            ["environments", "Templates"],
            ["environments/instances", "Instances"],
            ["environments/providers", "Providers"],
          ],
        ],
      ],
    },
    {
      label: "Integrations",
      entries: [
        ["application-accounts", "Application accounts", Cable],
        [
          "connectors",
          "Connectors",
          Plug,
          [
            ["connectors", "Connections"],
            ["connectors/providers", "Providers"],
          ],
        ],
        ["mcp", "MCP connections", Network],
      ],
    },
    {
      label: "Observe",
      entries: [["traces", "Traces", Activity]],
    },
  ];
  const logout = useMutation({
    mutationFn: auth.logout,
    onSuccess: () => navigate("/login", { replace: true }),
  });
  const current = groups
    .flatMap((group) => group.entries)
    .find(([path]) => location.pathname.includes(`/${path}`));
  const currentChild = current?.[3]?.find(
    ([path]) => path !== current[0] && location.pathname === `${base}/${path}`,
  );
  return (
    <div className={styles.shell} data-contextual={contextual}>
      {open && !contextual && (
        <button
          className={styles.scrim}
          aria-label={t("Close navigation")}
          onClick={() => setOpen(false)}
        />
      )}
      {!contextual && (
        <aside
          className={styles.sidebar}
          data-open={open}
          aria-label={t("Main navigation")}
        >
          <div className={styles.brand}>
            <Logo alt="" />
            <Wordmark />
            <Button
              variant="ghost"
              className={styles.mobileClose}
              aria-label={t("Close navigation")}
              icon={<X size={16} />}
              onClick={() => setOpen(false)}
            />
          </div>
          <div className={styles.workspace}>
            <Menu
              label={t("Workspace menu")}
              align="start"
              trigger={
                <button
                  className={styles.workspaceTrigger}
                  aria-label={t("Workspace menu")}
                >
                  <span className={styles.workspaceIcon}>
                    {context.workspace.image_url ? (
                      <img src={context.workspace.image_url} alt="" />
                    ) : (
                      context.workspace.name.slice(0, 2).toUpperCase()
                    )}
                  </span>
                  <span className={styles.workspaceName}>
                    {context.workspace.name}
                  </span>
                  <ChevronDown size={13} />
                </button>
              }
              groups={[
                {
                  actions: [
                    {
                      id: "settings",
                      label: t("Settings"),
                      icon: <Settings size={15} />,
                      onSelect: () => {
                        setOpen(false);
                        navigate("/settings/profile?section=preferences");
                      },
                    },
                  ],
                },
                {
                  actions: [
                    {
                      id: "switch-workspace",
                      label: t("Switch workspace"),
                      items: context.workspaces.map((item) => ({
                        id: item.id,
                        label: item.name,
                        selected: item.id === context.workspace.id,
                        onSelect: () => {
                          if (item.id !== context.workspace.id) {
                            void cache.cancelQueries();
                            cache.removeQueries({
                              predicate: (query) =>
                                query.queryKey.includes(context.workspace.id),
                            });
                            navigate(`/workspaces/${item.id}/agents`);
                          }
                          setOpen(false);
                        },
                      })),
                    },
                  ],
                },
              ]}
            />
          </div>
          <nav>
            {groups.map((group) => (
              <div className={styles.navGroup} key={group.label}>
                {group.label && (
                  <span className={styles.groupLabel}>{t(group.label)}</span>
                )}
                {group.entries.map(([path, label, Icon, children]) => (
                  <div key={path}>
                    <NavLink
                      to={`${base}/${path}`}
                      onClick={() => setOpen(false)}
                      className={({ isActive }) =>
                        `${styles.navItem} ${isActive ? styles.active : ""}`
                      }
                    >
                      <Icon size={16} strokeWidth={1.7} />
                      <span>{t(label)}</span>
                      {children && (
                        <ChevronDown size={12} className={styles.navChevron} />
                      )}
                    </NavLink>
                    {children &&
                      (location.pathname === `${base}/${path}` ||
                        location.pathname.startsWith(`${base}/${path}/`)) && (
                        <div className={styles.navChildren}>
                          {children.map(([childPath, childLabel]) => (
                            <NavLink
                              key={childPath}
                              to={`${base}/${childPath}`}
                              end
                              onClick={() => setOpen(false)}
                              className={({ isActive }) =>
                                `${styles.navChild} ${isActive ? styles.active : ""}`
                              }
                            >
                              {t(childLabel)}
                            </NavLink>
                          ))}
                        </div>
                      )}
                  </div>
                ))}
              </div>
            ))}
          </nav>
          <div className={styles.sidebarFooter}>
            <Menu
              label={t("Your account")}
              align="start"
              trigger={
                <button className={styles.user}>
                  <Avatar name={user.name} url={user.image_url} />
                  <span>
                    <strong>{user.name}</strong>
                  </span>
                  <ChevronsUpDown size={14} />
                </button>
              }
              groups={[
                {
                  label: user.email,
                  actions: [
                    {
                      id: "logout",
                      label: t("Sign out"),
                      icon: <LogOut size={15} />,
                      disabled: logout.isPending,
                      onSelect: () => logout.mutate(),
                    },
                  ],
                },
              ]}
            />
          </div>
        </aside>
      )}
      <div className={styles.main}>
        {!contextual && (
          <header className={styles.topbar}>
            {!contextual && (
              <Button
                className={styles.mobileMenu}
                aria-label={t("Open navigation")}
                variant="ghost"
                icon={<MenuIcon size={18} />}
                onClick={() => setOpen(true)}
              />
            )}
            <strong>{t(current?.[1] ?? "Settings")}</strong>
            {currentChild && (
              <>
                <span className={styles.slash}>/</span>
                <strong>{t(currentChild[1])}</strong>
              </>
            )}
          </header>
        )}
        <ErrorNotice error={logout.error} />
        <main id="main-content">
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
export function Avatar({ name, url }: { name: string; url?: string | null }) {
  return (
    <span className={styles.avatar}>
      {url ? <img src={url} alt="" /> : name.slice(0, 2).toUpperCase()}
    </span>
  );
}
