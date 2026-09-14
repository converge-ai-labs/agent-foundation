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
  useSidebar,
  Wordmark,
} from "a13n-ui";
import { CaretDownIcon, ListIcon, XIcon } from "@phosphor-icons/react";
import { Suspense, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation } from "react-router";
import { Loading } from "../shared/feedback";
import { AccountMenu } from "./account-menu";
import { navigationGroups } from "./navigation";
import { useWorkspace } from "./workspace";
import { WorkspaceMenu } from "./workspace-menu";
function PageOutlet() {
  return (
    <main id="main-content" className="min-h-0 min-w-0 flex-1">
      <Suspense fallback={<Loading page />}>
        <Outlet />
      </Suspense>
    </main>
  );
}
export function Shell() {
  const location = useLocation();
  const contextual =
    /^\/[^/]+\/[^/]+\/settings(\/|$)/.test(location.pathname) ||
    location.pathname === "/settings/profile" ||
    location.pathname === "/organization/settings";
  if (contextual)
    return (
      <div className="min-h-svh bg-background">
        <PageOutlet />
      </div>
    );
  return (
    <SidebarProvider style={{ "--sidebar-width": "14.5rem" } as CSSProperties}>
      <WorkspaceNavigation />
    </SidebarProvider>
  );
}
function WorkspaceNavigation() {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const { pathname } = useLocation();
  const { setOpenMobile } = useSidebar();
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
        <SidebarHeader className="gap-2 px-2 pt-4">
          <div className="flex items-center gap-2 px-2 text-xl text-foreground">
            <Logo alt="" width={28} height={28} />
            <Wordmark />
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
          <WorkspaceMenu onNavigate={close} />
        </SidebarHeader>
        <SidebarContent>
          <nav aria-label={t("Resources")}>
            {navigationGroups.map((group) => (
              <SidebarGroup key={group.label}>
                {group.label && (
                  <SidebarGroupLabel>{t(group.label)}</SidebarGroupLabel>
                )}
                <SidebarMenu>
                  {group.entries.map(([path, label, Icon, children]) => {
                    const active =
                      pathname === destination(path) ||
                      pathname.startsWith(`${destination(path)}/`);
                    return (
                      <SidebarMenuItem key={path}>
                        <SidebarMenuButton
                          isActive={active}
                          render={
                            <NavLink to={destination(path)} onClick={close} />
                          }
                        >
                          <Icon weight={active ? "duotone" : "regular"} />
                          <span>{t(label)}</span>
                          {children && (
                            <CaretDownIcon className="ml-auto size-3" />
                          )}
                        </SidebarMenuButton>
                        {children && active && (
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
          <AccountMenu onNavigate={close} />
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="h-dvh min-w-0">
        <div className="flex shrink-0 px-4 pt-4 sm:px-7 md:hidden">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label={t("Open navigation")}
            onClick={() => setOpenMobile(true)}
          >
            <ListIcon />
          </Button>
        </div>
        <PageOutlet />
      </SidebarInset>
    </>
  );
}
