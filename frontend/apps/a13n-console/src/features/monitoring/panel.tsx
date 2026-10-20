import { DotsSixVerticalIcon, InfoIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../shared/feedback";
import type { Panel, Target, TimeRange } from "./api";
import { MetricsChart } from "./chart";
import { statusLabels } from "./labels";
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
        {(!!data.notices.length || !!panel.description) && (
          <details className={styles.notices}>
            <summary aria-label={t("Query details")} title={t("Query details")}>
              <InfoIcon size={16} />
            </summary>
            <div className={`${styles.noticeBody} a13n-scrollbar`}>
              {panel.description && <p>{panel.description}</p>}
              {data.notices.map((notice) => (
                <p key={JSON.stringify(notice)}>
                  {typeof notice === "string"
                    ? (statusLabels(t)[notice] ?? t(notice))
                    : t(notice.message, notice.values)}
                </p>
              ))}
            </div>
          </details>
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
    </section>
  );
}
