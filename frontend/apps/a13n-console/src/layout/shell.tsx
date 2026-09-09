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
  User,
  Building2,
  LogOut,
  Languages,
  Menu as MenuIcon,
  X,
} from "lucide-react";
import { Button, Logo, Menu, Picker } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/context";
import { useWorkspace } from "./workspace";
import { ErrorNotice, Loading } from "../shared/feedback";
import styles from "./shell.module.css";

export function Shell() {
  const { t, i18n } = useTranslation(),
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
  const groups: { label: string; entries: [string, string, LucideIcon][] }[] = [
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
        ["models", "Models", Boxes],
        ["skills", "Skills", Sparkles],
        ["assets", "Assets", File],
        ["environments", "Environments", Monitor],
      ],
    },
    {
      label: "Integrations",
      entries: [
        ["application-accounts", "Application accounts", Cable],
        ["connectors", "Connectors", Plug],
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
            <strong>a13n</strong>
            <span>Console</span>
            <Button
              variant="ghost"
              className={styles.mobileClose}
              aria-label={t("Close navigation")}
              icon={<X size={16} />}
              onClick={() => setOpen(false)}
            />
          </div>
          <div className={styles.workspace}>
            <span>{t("Workspace")}</span>
            <Picker
              label={t("Switch workspace")}
              placeholder={t("Select workspace")}
              emptyMessage={t("No workspaces found")}
              value={context.workspace.id}
              groups={[
                {
                  label: t("Workspaces"),
                  options: context.workspaces.map((item) => ({
                    value: item.id,
                    label: item.name,
                    icon: (
                      <span className={styles.workspaceIcon}>
                        {item.name.slice(0, 1).toUpperCase()}
                      </span>
                    ),
                  })),
                },
              ]}
              onValueChange={(id) => {
                void cache.cancelQueries();
                cache.removeQueries({
                  predicate: (query) =>
                    query.queryKey.includes(context.workspace.id),
                });
                navigate(`/workspaces/${id}/agents`);
                setOpen(false);
              }}
            />
          </div>
          <nav>
            {groups.map((group) => (
              <div className={styles.navGroup} key={group.label}>
                {group.label && (
                  <span className={styles.groupLabel}>{t(group.label)}</span>
                )}
                {group.entries.map(([path, label, Icon]) => (
                  <NavLink
                    key={path}
                    to={`${base}/${path}`}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      `${styles.navItem} ${isActive ? styles.active : ""}`
                    }
                  >
                    <Icon size={16} strokeWidth={1.7} />
                    <span>{t(label)}</span>
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
          <div className={styles.sidebarFooter}>
            <NavLink
              to={`${base}/settings`}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `${styles.navItem} ${isActive ? styles.active : ""}`
              }
            >
              <Settings size={16} />
              {t("Workspace settings")}
            </NavLink>
            <Menu
              label={t("Your account")}
              align="start"
              trigger={
                <button className={styles.user}>
                  <Avatar name={user.name} url={user.image_url} />
                  <span>
                    <strong>{user.name}</strong>
                    <small>{user.email}</small>
                  </span>
                  <ChevronsUpDown size={14} />
                </button>
              }
              groups={[
                {
                  actions: [
                    {
                      id: "profile",
                      label: t("Personal settings"),
                      icon: <User size={15} />,
                      onSelect: () => navigate("/settings/profile"),
                    },
                    ...(context.organizationAdmin
                      ? [
                          {
                            id: "organization",
                            label: t("Organization settings"),
                            icon: <Building2 size={15} />,
                            onSelect: () => navigate("/organization/settings"),
                          },
                        ]
                      : []),
                  ],
                },
                {
                  actions: [
                    {
                      id: "language",
                      label:
                        i18n.resolvedLanguage === "en" ? "简体中文" : "English",
                      icon: <Languages size={15} />,
                      onSelect: () => {
                        void i18n.changeLanguage(
                          i18n.resolvedLanguage === "en" ? "zh-CN" : "en",
                        );
                      },
                    },
                  ],
                },
                {
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
          <span>{context.workspace.name}</span>
          <span className={styles.slash}>/</span>
          <strong>{t(current?.[1] ?? "Settings")}</strong>
          <span className={styles.topbarEnd}>{context.organization.name}</span>
        </header>
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
