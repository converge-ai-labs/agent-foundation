import {
  Sheet,
  SheetDescription,
  SheetHeader,
  SheetPanel,
  SheetPopup,
  SheetTitle,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useMountedModal } from "../../shared/dialogs";
import type { usagePage, UsageAgent, UsageMember } from "./navigation";
import { Breakdown } from "./breakdown";
import styles from "./usage.module.css";

export function SessionUsageSheet({
  agent,
  window,
  member,
  onClose,
  page,
}: {
  agent: UsageAgent;
  window: { start: string; end: string };
  member: UsageMember | null;
  onClose: () => void;
  page: ReturnType<typeof usagePage>;
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
              page={page}
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
