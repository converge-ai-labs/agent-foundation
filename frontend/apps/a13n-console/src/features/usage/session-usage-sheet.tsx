import {
  Sheet,
  SheetDescription,
  SheetHeader,
  SheetPanel,
  SheetPopup,
  SheetTitle,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { useMountedModal } from "../../shared/dialogs";
import { Breakdown } from "./breakdown";
import styles from "./usage.module.css";

export function SessionUsageSheet({
  agent,
  window,
  member,
  onClose,
}: {
  agent: Schema["AgentUsage"];
  window: { start: string; end: string };
  member: Schema["MemberUsage"] | null;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { modalProps } = useMountedModal(onClose);
  const date = (value: string) =>
    new Date(value).toLocaleDateString(i18n.resolvedLanguage);
  return (
    <Sheet {...modalProps}>
      <SheetPopup
        className="w-full max-w-[960px] sm:w-[calc(100%-48px)]"
        closeProps={{ "aria-label": t("Close") }}
      >
        <SheetHeader className="pr-12">
          <SheetTitle>{t("Session usage")}</SheetTitle>
          <p className={styles.sessionAgent}>{agent.name}</p>
          <SheetDescription>
            {date(window.start)} – {date(window.end)}
            {member && ` · ${member.name}`}
          </SheetDescription>
          <p className={styles.footnote}>
            {t(
              "Sessions for this agent in the selected period, sorted by spend (highest first).",
            )}
          </p>
        </SheetHeader>
        <SheetPanel className={styles.sessionPanel}>
          <div className={styles.breakdown} data-group="sessions">
            <Breakdown
              window={window}
              group="sessions"
              agentId={agent.agent_id}
              principalId={member?.principal_id}
            />
          </div>
        </SheetPanel>
      </SheetPopup>
    </Sheet>
  );
}
