import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "a13n-ui";
import {
  CaretUpDownIcon,
  CheckIcon,
  GearSixIcon,
  PlusIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { UserAvatar } from "./avatar";
import { workspacePath } from "../shared/paths";
import { useWorkspace } from "./workspace";
import styles from "./layout.module.css";

export function WorkspaceMenu({
  onNavigate,
  compact = false,
}: {
  onNavigate: () => void;
  compact?: boolean;
}) {
  const { t } = useTranslation(),
    context = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient();
  const open = (path: string) => {
    onNavigate();
    navigate(path);
  };
  return (
    <Menu>
      <MenuTrigger
        openOnHover
        delay={100}
        closeDelay={150}
        aria-label={t("Workspace menu")}
        render={
          compact ? (
            <Button variant="ghost" size="icon-sm" className="mx-auto" />
          ) : (
            <Button variant="ghost" className="w-full justify-start" />
          )
        }
      >
        <UserAvatar
          name={context.workspace.name}
          url={context.workspace.image_url}
          className="size-5 rounded-md text-[10px]"
        />
        {compact ? (
          <span className="sr-only">{context.workspace.name}</span>
        ) : (
          <>
            <span className="min-w-0 flex-1 truncate text-left">
              {context.workspace.name}
            </span>
            <CaretUpDownIcon aria-hidden="true" />
          </>
        )}
      </MenuTrigger>
      <MenuPopup
        align="start"
        side={compact ? "right" : "bottom"}
        className={compact ? "min-w-56" : "w-(--anchor-width) min-w-56"}
      >
        <MenuGroup>
          <MenuGroupLabel>{context.organization.name}</MenuGroupLabel>
          {context.workspaces.map((item) => {
            const current = item.id === context.workspace.id;
            return (
              <MenuItem
                key={item.id}
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  if (!current) {
                    void cache.cancelQueries();
                    cache.removeQueries({
                      predicate: (query) =>
                        query.queryKey.includes(context.workspace.id),
                    });
                    navigate(`${workspacePath(item)}/agents`);
                  }
                  onNavigate();
                }}
              >
                <span aria-hidden="true">
                  <UserAvatar
                    name={item.name}
                    url={item.image_url}
                    className="size-5 rounded-md text-[10px]"
                  />
                </span>
                <span className={styles.menuName}>{item.name}</span>
                {current && (
                  <CheckIcon aria-hidden="true" className="ms-auto" />
                )}
              </MenuItem>
            );
          })}
        </MenuGroup>
        <MenuSeparator />
        <MenuGroup>
          <MenuItem onClick={() => open(`${context.basePath}/settings`)}>
            <GearSixIcon aria-hidden="true" />
            {t("Workspace settings")}
          </MenuItem>
          {context.organizationAdmin && (
            <MenuItem onClick={() => open("/organization/settings/workspaces")}>
              <PlusIcon aria-hidden="true" />
              {t("Create workspace")}
            </MenuItem>
          )}
        </MenuGroup>
      </MenuPopup>
    </Menu>
  );
}
