import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { UNKNOWN } from "../../shared/unknown";
import { conversationQueries, type ContextScope } from "./api";
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

export function formatContextTokens(value: number | null): string {
  if (value === null) return UNKNOWN;
  const unit = value >= 1_000_000 ? "m" : "k";
  const scale = unit === "m" ? 1_000_000 : 1000;
  return `${Number((value / scale).toFixed(1))}${unit}`;
}

/** Reported usage is a last-call estimate, independent of the draft and cumulative cost. */
export function ContextWindow({
  scope,
  live = false,
}: {
  scope: ContextScope;
  live?: boolean;
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
  return (
    <div className={styles.details}>
      <span className={styles.heading}>{t("Context window")}</span>
      <div className={styles.values}>
        <span>
          {t("{{usage}} used ({{remaining}} left)", {
            usage: label,
            remaining:
              percent === null ? UNKNOWN : `${Math.max(0, 100 - percent)}%`,
          })}
        </span>
        <span title={`${exact(used)} / ${exact(capacity)}`}>
          {t("{{used}} / {{capacity}} tokens used", {
            used: formatContextTokens(used),
            capacity: formatContextTokens(capacity),
          })}
        </span>
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
}
