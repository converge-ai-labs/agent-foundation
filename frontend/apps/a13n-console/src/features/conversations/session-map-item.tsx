import { Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ChatCircleIcon, WrenchIcon, BrainIcon } from "@phosphor-icons/react";
import { StateBadge } from "../../shared/feedback";
import type { PresentedItem } from "./projection";
import styles from "./session-map.module.css";

export function MapItem({ item }: { item: PresentedItem }) {
  const { t } = useTranslation();
  const label =
    item.kind === "tool_call"
      ? item.toolName || t("Tool call")
      : item.kind === "reasoning_message"
        ? t("Reasoning summary")
        : t("Agent reply");
  const Icon =
    item.kind === "tool_call"
      ? WrenchIcon
      : item.kind === "reasoning_message"
        ? BrainIcon
        : ChatCircleIcon;
  return (
    <li>
      <Tooltip>
        <TooltipTrigger
          render={<span tabIndex={0} />}
          className={styles.itemRow}
        >
          <Icon size={12} aria-hidden="true" />
          <span className={styles.itemLabel}>{label}</span>
          <span className={styles.itemExcerpt}>{item.text}</span>
        </TooltipTrigger>
        <TooltipPopup
          role="tooltip"
          side="left"
          sideOffset={16}
          className={styles.preview}
        >
          <div className={styles.previewContent}>
            <div className={styles.runTitle}>
              <strong>{label}</strong>
              <StateBadge state={item.state} />
            </div>
            <p className={styles.previewInput}>
              {item.text || item.arguments || t("No text content")}
            </p>
            {item.result !== undefined && (
              <p className={styles.previewInput}>
                {typeof item.result === "string"
                  ? item.result
                  : JSON.stringify(item.result, null, 2)}
              </p>
            )}
          </div>
        </TooltipPopup>
      </Tooltip>
    </li>
  );
}
