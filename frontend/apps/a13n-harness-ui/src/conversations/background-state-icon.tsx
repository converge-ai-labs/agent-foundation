import {
  CheckCircle,
  CircleNotch,
  Clock,
  ClockCountdown,
  MinusCircle,
  Question,
  WarningCircle,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";
import { Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import { useTranslation } from "../i18n";
import styles from "./background.module.css";

const states: Record<string, [string, Icon, string]> = {
  running: [
    "Running",
    CircleNotch,
    "text-info-foreground motion-safe:animate-spin",
  ],
  queued: ["Queued", Clock, "text-warning-foreground"],
  succeeded: ["Succeeded", CheckCircle, "text-success-foreground"],
  exited: ["Exited", CheckCircle, "text-success-foreground"],
  failed: ["Failed", XCircle, "text-destructive-foreground"],
  cancelled: ["Cancelled", MinusCircle, "text-muted-foreground"],
  lost: ["Lost", WarningCircle, "text-warning-foreground"],
  signaled: ["Signaled", WarningCircle, "text-warning-foreground"],
  timed_out: ["Timed out", ClockCountdown, "text-warning-foreground"],
  unavailable: ["Unavailable", Question, "text-muted-foreground"],
};

export function BackgroundStateIcon({
  state,
  focusable = false,
}: {
  state: string;
  focusable?: boolean;
}) {
  const { t } = useTranslation();
  const [message, Glyph, color] = states[state] ?? [
    state,
    Question,
    "text-muted-foreground",
  ];
  const label = t(message);
  return (
    <Tooltip>
      <TooltipTrigger
        render={<span role="img" aria-label={label} />}
        tabIndex={focusable ? 0 : -1}
        className={styles.stateIcon}
      >
        <Glyph aria-hidden="true" size={15} weight="bold" className={color} />
      </TooltipTrigger>
      <TooltipPopup>{label}</TooltipPopup>
    </Tooltip>
  );
}
