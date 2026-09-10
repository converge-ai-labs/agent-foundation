import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuGroup,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "a13n-ui";
import { CheckIcon, CaretDownIcon, GearSixIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { UserAvatar } from "./avatar";
import { workspacePath } from "../shared/paths";
import { useWorkspace } from "./workspace";

export function WorkspaceMenu({ onNavigate }: { onNavigate: () => void }) {
  const { t } = useTranslation(),
    context = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient();
  return (
    <Menu>
      <MenuTrigger
        openOnHover
        delay={100}
        closeDelay={150}
        aria-label={t("Workspace menu")}
        render={<Button variant="ghost" className="w-full justify-start" />}
      >
        <UserAvatar
          name={context.workspace.name}
          url={context.workspace.image_url}
          className="size-5 rounded-md text-[10px]"
        />
        <span className="min-w-0 flex-1 truncate text-left">
          {context.workspace.name}
        </span>
        <CaretDownIcon aria-hidden="true" />
      </MenuTrigger>
      <MenuPopup align="start" className="w-(--anchor-width)">
        <MenuGroup>
          <MenuItem
            onClick={() => {
              onNavigate();
              navigate(`${context.basePath}/settings?section=profile`);
            }}
          >
            <GearSixIcon aria-hidden="true" />
            {t("Settings")}
          </MenuItem>
          <MenuSeparator />
          <MenuSub>
            <MenuSubTrigger>{t("Switch workspace")}</MenuSubTrigger>
            <MenuSubPopup>
              <MenuGroup>
                {context.workspaces.map((item) => (
                  <MenuItem
                    key={item.id}
                    aria-current={
                      item.id === context.workspace.id ? "true" : undefined
                    }
                    onClick={() => {
                      if (item.id !== context.workspace.id) {
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
                    {item.name}
                    {item.id === context.workspace.id && (
                      <CheckIcon aria-hidden="true" className="ms-auto" />
                    )}
                  </MenuItem>
                ))}
              </MenuGroup>
            </MenuSubPopup>
          </MenuSub>
        </MenuGroup>
      </MenuPopup>
    </Menu>
  );
}
