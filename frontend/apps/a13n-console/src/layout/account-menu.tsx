import { displayEmail } from "../shared/identity/display-email";
import {
  Button,
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuPrimitive,
  MenuRadioGroup,
  MenuSeparator,
  MenuTrigger,
} from "a13n-ui";
import {
  CaretUpDownIcon,
  SignOutIcon,
  GearSixIcon,
  TranslateIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useAuth } from "../auth/context";
import { useSignOut } from "../auth/sign-out";
import { ErrorToast } from "../shared/feedback";
import { UserAvatar } from "./avatar";
import { useAppearance } from "./appearance";
import { useAccess } from "./workspace";
import { workspacePath } from "../shared/paths";
import styles from "./layout.module.css";

export function AccountMenu({
  onNavigate,
  compact = false,
}: {
  onNavigate: () => void;
  compact?: boolean;
}) {
  const { t } = useTranslation(),
    auth = useAuth(),
    navigate = useNavigate();
  const { workspace } = useAccess();
  const { theme, setTheme } = useAppearance();
  const user = auth.data!.user.value;
  const email = displayEmail(user);
  const logout = useSignOut();
  const open = (path: string) => {
    onNavigate();
    navigate(path);
  };
  return (
    <>
      <Menu>
        <MenuTrigger
          delay={100}
          closeDelay={150}
          render={
            compact ? (
              <Button
                variant="ghost"
                size="icon-sm"
                className={styles.railButton}
              />
            ) : (
              <Button
                variant="ghost"
                className={`${styles.navigationRow} ${styles.accountTrigger}`}
              />
            )
          }
        >
          <UserAvatar
            name={user.name}
            url={user.image_url}
            className={compact ? "size-6" : "size-7"}
          />
          {compact ? (
            <span className="sr-only">{user.name}</span>
          ) : (
            <>
              <span className="min-w-0 flex-1 truncate text-left">
                {user.name}
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
          <div className={styles.account}>
            <UserAvatar name={user.name} url={user.image_url} />
            <span className={styles.accountCopy}>
              <strong title={user.name}>{user.name}</strong>
              {email && <small title={email}>{email}</small>}
            </span>
          </div>
          <MenuSeparator />
          <MenuGroup>
            <MenuGroupLabel>{t("Appearance")}</MenuGroupLabel>
            <MenuRadioGroup
              aria-label={t("Color theme")}
              className={styles.themeOptions}
              value={theme}
              onValueChange={(value) => {
                if (value === "light" || value === "dark" || value === "system")
                  setTheme(value);
              }}
            >
              {[
                { value: "light", label: t("Light") },
                { value: "dark", label: t("Dark") },
                { value: "system", label: t("Follow system") },
              ].map(({ value, label }) => (
                <MenuPrimitive.RadioItem
                  key={value}
                  value={value}
                  closeOnClick={false}
                  className={styles.themeOption}
                >
                  {label}
                </MenuPrimitive.RadioItem>
              ))}
            </MenuRadioGroup>
          </MenuGroup>
          <MenuSeparator />
          <MenuGroup>
            <MenuItem
              onClick={() =>
                open(
                  workspace
                    ? `${workspacePath(workspace)}/settings`
                    : "/settings",
                )
              }
            >
              <GearSixIcon aria-hidden="true" />
              {t("Settings")}
            </MenuItem>
            <MenuItem onClick={() => open("/settings/language")}>
              <TranslateIcon aria-hidden="true" />
              {t("Language")}
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
      <ErrorToast error={logout.error} />
    </>
  );
}
