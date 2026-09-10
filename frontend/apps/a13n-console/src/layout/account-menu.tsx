import { useMutation } from "@tanstack/react-query";
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
  SignOutIcon,
  GearSixIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useAuth } from "../auth/context";
import { ErrorNotice } from "../shared/feedback";
import { UserAvatar } from "./avatar";

export function AccountMenu({ onNavigate }: { onNavigate: () => void }) {
  const { t } = useTranslation(),
    auth = useAuth(),
    navigate = useNavigate();
  const user = auth.data!.user.value;
  const logout = useMutation({
    mutationFn: auth.logout,
    onSuccess: () => navigate("/login", { replace: true }),
  });
  return (
    <>
      <Menu>
        <MenuTrigger
          openOnHover
          delay={100}
          closeDelay={150}
          render={
            <Button
              variant="ghost"
              className="h-auto w-full justify-start py-2"
            />
          }
        >
          <UserAvatar name={user.name} url={user.image_url} />
          <span className="min-w-0 flex-1 truncate text-left">{user.name}</span>
          <CaretUpDownIcon aria-hidden="true" />
        </MenuTrigger>
        <MenuPopup align="start" className="w-(--anchor-width)">
          <MenuGroup>
            <MenuGroupLabel>{user.email}</MenuGroupLabel>
            <MenuItem
              onClick={() => {
                onNavigate();
                navigate("/settings/profile?section=profile");
              }}
            >
              <GearSixIcon aria-hidden="true" />
              {t("Settings")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
            >
              <SignOutIcon aria-hidden="true" />
              {t("Sign out")}
            </MenuItem>
          </MenuGroup>
        </MenuPopup>
      </Menu>
      <ErrorNotice error={logout.error} />
    </>
  );
}
