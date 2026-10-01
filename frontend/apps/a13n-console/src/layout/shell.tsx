import {
  Button,
  Logo,
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
  useSidebar,
  Wordmark,
} from "a13n-ui";
import {
  CaretDownIcon,
  GearSixIcon,
  type Icon as PhosphorIcon,
  ListIcon,
  SidebarSimpleIcon,
  XIcon,
} from "@phosphor-icons/react";
import { Suspense, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation } from "react-router";
import { Loading } from "../shared/feedback";
import { routeSkeleton } from "./navigation";
import { AccountMenu } from "./account-menu";
import { navigationGroups } from "./navigation";
import { usePageTitle } from "./page-title";
import { useAccess, useWorkspace } from "./workspace";
import { WorkspaceMenu } from "./workspace-menu";
const SIDEBAR_STATE_KEY = "a13n-console-sidebar";
function PageOutlet() {
  const { pathname } = useLocation();
  // Route transitions to a known destination keep its layout while it loads.
  const skeleton = routeSkeleton(pathname);
  return (
    <main id="main-content" className="min-h-0 min-w-0 flex-1">
      <Suspense
        fallback={
          skeleton ? (
            <Loading
              page
              variant={skeleton.variant}
              columns={skeleton.columns}
            />
          ) : (
            <Loading page />
          )
        }
      >
        <Outlet />
      </Suspense>
    </main>
  );
}
export function Shell() {
  const location = useLocation();
  const { workspace } = useAccess();
  usePageTitle(workspace?.name);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_STATE_KEY) === "collapsed";
    } catch {
      return false;
    }
  });
  const updateCollapsed = (value: boolean) => {
    setCollapsed(value);
    try {
      localStorage.setItem(SIDEBAR_STATE_KEY, value ? "collapsed" : "expanded");
    } catch {
      /* Keep the preference for this visit when storage is unavailable. */
    }
  };
  const contextual =
    /^\/[^/]+\/[^/]+\/settings(\/|$)/.test(location.pathname) ||
    /^\/settings(\/|$)/.test(location.pathname) ||
    /^\/organization\/settings(\/|$)/.test(location.pathname);
  if (contextual)
    return (
      <div className="min-h-svh bg-background">
        <PageOutlet />
      </div>
    );
  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": collapsed ? "3.25rem" : "14.5rem",
        } as CSSProperties
      }
    >
      <WorkspaceNavigation
        collapsed={collapsed}
        onCollapsedChange={updateCollapsed}
      />
    </SidebarProvider>
  );
}
function WorkspaceNavigation({
  collapsed,
  onCollapsedChange,
}: {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const { pathname } = useLocation();
  const { setOpenMobile, isMobile } = useSidebar();
  const rail = collapsed && !isMobile;
  const base = basePath;
  const destination = (path: string) =>
    path.startsWith("/") ? path : `${base}/${path}`;
  const close = () => setOpenMobile(false);
  return (
    <>
      <Sidebar
        role="complementary"
        aria-label={t("Main navigation")}
        title={t("Main navigation")}
        description={t("Workspace navigation")}
      >
        <SidebarHeader className="gap-3 px-2 pt-4">
          {rail ? (
            <Button
              variant="ghost"
              size="icon-sm"
              className="group/logo mx-auto"
              aria-label={t("Expand navigation")}
              onClick={() => onCollapsedChange(false)}
            >
              <Logo
                alt=""
                width={22}
                height={22}
                className="size-[22px] group-hover/logo:hidden group-focus-visible/logo:hidden"
              />
              <SidebarSimpleIcon
                weight="duotone"
                className="hidden size-4 group-hover/logo:block group-focus-visible/logo:block"
              />
            </Button>
          ) : (
            <div className="flex items-center gap-2 px-2 text-foreground text-xl">
              <Logo alt="" width={28} height={28} />
              <Wordmark />
              <Button
                variant="ghost"
                size="icon-sm"
                className="ml-auto hidden md:inline-flex"
                aria-label={t("Collapse navigation")}
                onClick={() => onCollapsedChange(true)}
              >
                <SidebarSimpleIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                className="ml-auto md:hidden"
                aria-label={t("Close navigation")}
                onClick={close}
              >
                <XIcon />
              </Button>
            </div>
          )}
          <WorkspaceMenu onNavigate={close} compact={rail} />
        </SidebarHeader>
        <SidebarContent>
          <nav aria-label={t("Resources")}>
            {navigationGroups.map((group) => (
              <SidebarGroup key={group.label}>
                {group.label &&
                  (rail ? (
                    <div
                      aria-hidden="true"
                      className="mx-auto my-1 h-px w-4 bg-sidebar-border"
                    />
                  ) : (
                    <SidebarGroupLabel>{t(group.label)}</SidebarGroupLabel>
                  ))}
                <SidebarMenu>
                  {group.entries.map(([path, label, Icon, children]) => {
                    const active =
                      pathname === destination(path) ||
                      pathname.startsWith(`${destination(path)}/`);
                    return (
                      <SidebarMenuItem key={path}>
                        <NavigationRow
                          to={destination(path)}
                          label={t(label)}
                          icon={Icon}
                          active={active}
                          rail={rail}
                          expandable={!!children}
                          onNavigate={close}
                        />
                        {!rail && children && active && (
                          <SidebarMenuSub className="mt-0.5 gap-0.5 border-0 py-0">
                            {children.map(([childPath, childLabel]) => (
                              <SidebarMenuSubItem key={childPath}>
                                <SidebarMenuSubButton
                                  isActive={pathname === `${base}/${childPath}`}
                                  render={
                                    <NavLink
                                      to={`${base}/${childPath}`}
                                      end
                                      onClick={close}
                                    />
                                  }
                                >
                                  {t(childLabel)}
                                </SidebarMenuSubButton>
                              </SidebarMenuSubItem>
                            ))}
                          </SidebarMenuSub>
                        )}
                      </SidebarMenuItem>
                    );
                  })}
                </SidebarMenu>
              </SidebarGroup>
            ))}
          </nav>
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <NavigationRow
                to={destination("settings")}
                label={t("Settings")}
                icon={GearSixIcon}
                active={pathname.startsWith(destination("settings"))}
                rail={rail}
                onNavigate={close}
              />
            </SidebarMenuItem>
          </SidebarMenu>
          <AccountMenu onNavigate={close} compact={rail} />
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="h-dvh min-w-0">
        <div className="flex shrink-0 px-(--a13n-page-gutter-mobile) pt-4 md:hidden">
          <Button
            variant="ghost"
            size="icon-sm"
            className="-ml-2 size-8 sm:size-8"
            aria-label={t("Open navigation")}
            onClick={() => setOpenMobile(true)}
          >
            <ListIcon className="size-4" />
          </Button>
        </div>
        <PageOutlet />
      </SidebarInset>
    </>
  );
}

/** One navigation row: a tooltip-only icon in the rail, icon and label when open. */
function NavigationRow({
  to,
  label,
  icon: Icon,
  active,
  rail,
  expandable = false,
  onNavigate,
}: {
  to: string;
  label: string;
  icon: PhosphorIcon;
  active: boolean;
  rail: boolean;
  expandable?: boolean;
  onNavigate: () => void;
}) {
  if (rail)
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <SidebarMenuButton
              isActive={active}
              className="justify-center"
              render={<NavLink to={to} onClick={onNavigate} />}
            />
          }
        >
          <Icon weight={active ? "duotone" : "regular"} />
          <span className="sr-only">{label}</span>
        </TooltipTrigger>
        <TooltipPopup side="right">{label}</TooltipPopup>
      </Tooltip>
    );
  // An open group highlights its selected child, never itself as well.
  const selected = active && !expandable;
  return (
    <SidebarMenuButton
      isActive={selected}
      render={<NavLink to={to} onClick={onNavigate} />}
    >
      <Icon weight={active ? "duotone" : "regular"} />
      <span>{label}</span>
      {expandable && (
        <CaretDownIcon
          className={active ? "ml-auto size-3" : "ml-auto size-3 -rotate-90"}
        />
      )}
    </SidebarMenuButton>
  );
}
