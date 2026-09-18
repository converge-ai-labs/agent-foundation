import { CheckIcon, HandIcon, ProhibitIcon } from "@phosphor-icons/react";
import { Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./agents.module.css";

export type PermissionChoice = "allow" | "ask" | "deny";

const options = [
  {
    value: "allow",
    icon: CheckIcon,
    description: "Run without asking for approval.",
  },
  {
    value: "ask",
    icon: HandIcon,
    description: "Ask for approval before each tool call.",
  },
  {
    value: "deny",
    icon: ProhibitIcon,
    description: "Never allow this tool to run.",
  },
] as const;

const labels = { allow: "Allow", ask: "Ask", deny: "Deny" } as const;

export function ToolPermissions({
  name,
  label,
  value,
  supported,
  readOnly = false,
  onChange,
}: {
  name: string;
  label?: string;
  value: Schema["ToolPermissionSetting"] | undefined;
  supported?: readonly string[];
  readOnly?: boolean;
  onChange: (permission: PermissionChoice) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={styles.toolsetPermissions}
      role="group"
      aria-label={label ?? t("{{tool}} permission", { tool: name })}
    >
      {options
        .filter((option) => !supported || supported.includes(option.value))
        .map(({ value: permission, icon: Icon, description }) => (
          <Tooltip key={permission}>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  disabled={readOnly}
                  className={`${styles.toolsetPermission} ${styles.toolsetPermissionLabel}`}
                  aria-label={t(permission)}
                  aria-pressed={
                    (value === "inherit" ? "allow" : (value ?? "allow")) ===
                    permission
                  }
                  onClick={() => onChange(permission)}
                />
              }
            >
              <Icon size={14} aria-hidden="true" />
              <span>{t(labels[permission])}</span>
            </TooltipTrigger>
            <TooltipPopup>{t(description)}</TooltipPopup>
          </Tooltip>
        ))}
    </div>
  );
}
