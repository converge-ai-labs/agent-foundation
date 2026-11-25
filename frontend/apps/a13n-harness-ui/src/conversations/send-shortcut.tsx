import { createContext, useContext, useState, type ReactNode } from "react";
import { useMediaQuery } from "a13n-ui";
import { useTranslation } from "../i18n";
import { modifierLabel, usesCommandKey } from "../platform";
import { readPreference, writePreference } from "../shell/preferences";
import { SettingsChoice } from "../shell/settings";

type SendShortcut = "enter" | "mod-enter";
const key = "send-shortcut";
const ShortcutContext = createContext({
  preference: "enter" as SendShortcut,
  select: (_value: SendShortcut) => {},
});

export function SendShortcutProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<SendShortcut>(() =>
    readPreference(key, "enter") === "mod-enter" ? "mod-enter" : "enter",
  );
  return (
    <ShortcutContext.Provider
      value={{
        preference,
        select(value) {
          setPreference(value);
          writePreference(key, value);
        },
      }}
    >
      {children}
    </ShortcutContext.Provider>
  );
}

export function SendShortcutChoice() {
  const { t } = useTranslation();
  const { preference, select } = useContext(ShortcutContext);
  return (
    <SettingsChoice
      label={t("Send messages with")}
      description={t("Saved in this browser.")}
      value={preference}
      onValueChange={(value) => {
        if (value === "enter" || value === "mod-enter") select(value);
      }}
      options={[
        { value: "enter", label: t("Enter (default)") },
        { value: "mod-enter", label: `${modifierLabel()} + Enter` },
      ]}
    />
  );
}

export function useComposerSendShortcut() {
  const { preference } = useContext(ShortcutContext);
  const touch = useMediaQuery("(pointer: coarse)");
  const plainEnterSends = preference === "enter" && !touch;
  const command = usesCommandKey();
  const modifiedEnter = `${command ? "Meta" : "Control"}+Enter`;
  return {
    touch,
    plainEnterSends,
    command,
    label: plainEnterSends ? "Enter" : `${command ? "⌘" : "Ctrl"} + Enter`,
    ariaKeyShortcuts: plainEnterSends
      ? `Enter ${modifiedEnter}`
      : modifiedEnter,
  };
}
