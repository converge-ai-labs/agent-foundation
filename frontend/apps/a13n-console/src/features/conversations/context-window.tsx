import { Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { UNKNOWN } from "../../shared/unknown";
import { conversationQueries, type ContextScope } from "./api";
import { formatTokens } from "./format";
import styles from "./context-window.module.css";

export function contextFigures(
  usage: Schema["ContextUsage"] | null | undefined,
) {
  const used =
    usage?.input_tokens != null && usage.output_tokens != null
      ? usage.input_tokens + usage.output_tokens
      : null;
  const capacity = usage?.context_window_tokens ?? null;
  const percent =
    used !== null && capacity !== null && capacity > 0
      ? Math.round((used / capacity) * 100)
      : null;
  return { used, capacity, percent };
}

/** Reported usage is a last-call estimate, independent of the draft and cumulative cost. */
export function ContextWindow({
  scope,
  live = false,
  compact = false,
}: {
  scope: ContextScope;
  live?: boolean;
  compact?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery({
    ...conversationQueries(client, workspace.id).context(scope),
    refetchInterval: live ? 5000 : false,
  });
  const { used, capacity, percent } = contextFigures(query.data);
  const label = percent === null ? UNKNOWN : `${percent}%`;
  const exact = (value: number | null) =>
    value === null ? UNKNOWN : value.toLocaleString(i18n.resolvedLanguage);
  const details = (
    <div className={styles.details}>
      <div className={styles.heading}>
        <strong>{t("Context window")}</strong>
        <span className={styles.source}>
          {"record_id" in scope ? t("This call") : t("Last call")}
        </span>
      </div>
      <div className={styles.balance}>
        <span className={styles.used}>
          <strong>{label}</strong>
          <span>{t("used")}</span>
        </span>
        {percent !== null && (
          <span className={styles.remaining}>
            {t("{{percent}}% remaining", {
              percent: Math.max(0, 100 - percent),
            })}
          </span>
        )}
      </div>
      <div className={styles.track} aria-hidden="true">
        <span style={{ width: `${Math.min(100, percent ?? 0)}%` }} />
      </div>
      <div className={styles.footer}>
        <span
          className={styles.tokens}
          title={`${exact(used)} / ${exact(capacity)}`}
        >
          {t("{{used}} / {{capacity}} tokens", {
            used: formatTokens(used),
            capacity: formatTokens(capacity),
          })}
        </span>
        <span className={styles.source}>{t("Configured limit")}</span>
      </div>
      {query.isPending && <p className={styles.note}>{t("Loading…")}</p>}
      {!query.isPending && !query.error && used === null && (
        <p className={styles.note}>{t("No context usage reported yet.")}</p>
      )}
      {used !== null && capacity === null && (
        <p className={styles.note}>{t("Context window size is unknown.")}</p>
      )}
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    </div>
  );
  if (!compact) return details;
  return (
    <Popover>
      <PopoverTrigger
        type="button"
        className={styles.trigger}
        aria-label={t("Context window: {{usage}}", { usage: label })}
        openOnHover
        delay={100}
        closeDelay={120}
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
          <circle className={styles.ringTrack} cx="10" cy="10" r="7" />
          <circle
            className={styles.ringValue}
            cx="10"
            cy="10"
            r="7"
            pathLength="100"
            strokeDasharray={`${Math.min(100, percent ?? 0)} 100`}
            transform="rotate(-90 10 10)"
          />
        </svg>
        <span>{label}</span>
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="end"
        sideOffset={10}
        className={styles.popup}
      >
        {details}
      </PopoverPopup>
    </Popover>
  );
}
