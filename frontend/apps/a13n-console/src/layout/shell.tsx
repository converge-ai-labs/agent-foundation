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
import { ChevronDown, Menu, X } from "lucide-react";
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
    <main id="main-content" className="min-w-0">
      <Suspense fallback={<Loading />}>
        <Outlet />
      </Suspense>
    </main>
  );
}
export function Shell() {
  const location = useLocation();
  const contextual =
    /^\/[^/]+\/[^/]+\/(sessions|settings)(\/|$)/.test(location.pathname) ||
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
  const { workspace, basePath } = useWorkspace();
  const { pathname } = useLocation();
  const { setOpenMobile } = useSidebar();
  const base = basePath;
  const close = () => setOpenMobile(false);
  const current = navigationGroups
    .flatMap((group) => group.entries)
    .find(
      ([path]) =>
        pathname === `${base}/${path}` ||
        pathname.startsWith(`${base}/${path}/`),
    );
  const currentChild = current?.[3]?.find(
    ([path]) => path !== current[0] && pathname === `${base}/${path}`,
  );
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
              <X />
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
                      pathname === `${base}/${path}` ||
                      pathname.startsWith(`${base}/${path}/`);
                    return (
                      <SidebarMenuItem key={path}>
                        <SidebarMenuButton
                          isActive={active}
                          render={
                            <NavLink to={`${base}/${path}`} onClick={close} />
                          }
                        >
                          <Icon />
                          <span>{t(label)}</span>
                          {children && (
                            <ChevronDown className="ml-auto size-3" />
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
      <SidebarInset className="min-w-0">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4 text-sm sm:px-7">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label={t("Open navigation")}
            onClick={() => setOpenMobile(true)}
          >
            <Menu />
          </Button>
          <strong className="font-medium">
            {t(current?.[1] ?? "Settings")}
          </strong>
          {currentChild && (
            <>
              <span className="text-muted-foreground">/</span>
              <strong className="font-medium">{t(currentChild[1])}</strong>
            </>
          )}
        </header>
        <PageOutlet />
      </SidebarInset>
    </>
  );
}
