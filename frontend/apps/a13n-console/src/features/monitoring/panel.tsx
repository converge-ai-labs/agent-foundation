import { DotsSixVerticalIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../shared/feedback";
import type { Panel, Target, TimeRange } from "./api";
import { MetricsChart } from "./chart";
import { usePanelData } from "./queries";
import styles from "./monitoring.module.css";

export function MetricsPanel({
  panel,
  target,
  range,
  revision,
  syncKey,
  onZoom,
  onEdit,
  onRemove,
}: {
  panel: Panel;
  target: Target | null;
  range: TimeRange;
  revision: number;
  syncKey: string;
  onZoom?: (range: TimeRange) => void;
  onEdit?: () => void;
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  const { data, errors, loading, stale } = usePanelData(
    panel,
    target,
    range,
    revision,
  );
  return (
    <section
      className={styles.panel}
      aria-label={panel.title}
      aria-busy={loading}
    >
      <div className={styles.panelHeader}>
        {onEdit && (
          <span
            className={`${styles.drag} dashboard-drag`}
            title={t("Drag panel")}
          >
            <DotsSixVerticalIcon size={18} />
          </span>
        )}
        <h3 title={panel.title}>{panel.title}</h3>
        {(loading || stale) && (
          <small role="status">
            {t(stale ? "Previous result" : "Loading…")}
          </small>
        )}
        {onEdit && (
          <Button size="xs" variant="ghost" onClick={onEdit}>
            {t("Edit")}
          </Button>
        )}
        {onRemove && (
          <Button size="xs" variant="ghost" onClick={onRemove}>
            {t("Remove")}
          </Button>
        )}
      </div>
      {panel.description && (
        <p className={styles.panelDescription}>{panel.description}</p>
      )}
      {errors.map((error, index) => (
        <ErrorNotice key={index} error={error} />
      ))}
      <MetricsChart
        panel={panel}
        series={data.series}
        range={range}
        syncKey={syncKey}
        onZoom={onZoom}
      />
      {!!data.notices.length && (
        <details className={styles.notices}>
          <summary>
            {t("Query details")} · {data.notices.length}
          </summary>
          {data.notices.map((notice) => (
            <p key={JSON.stringify(notice)}>
              {typeof notice === "string"
                ? t(notice)
                : t(notice.message, notice.values)}
            </p>
          ))}
        </details>
      )}
    </section>
  );
}
